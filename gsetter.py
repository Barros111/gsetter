#!/usr/bin/env python3
"""Gera acessores Java através de uma árvore sintática, sem reformatar o ficheiro."""

import argparse
import difflib
import os
from pathlib import Path
import shutil
import sys
import tempfile

try:
    from tree_sitter import Language, Parser
    import tree_sitter_java
except ImportError:
    sys.exit('Dependências em falta. Execute: python -m pip install -r requirements.txt')


IGNORED = {
    '.git', '.hg', '.svn', '.bzr', '.gradle', '.idea', '.venv', 'venv',
    'node_modules', 'vendor', 'target', 'build', 'out', 'bin', 'dist',
    '__pycache__', '.m2', '.settings', '.tmp',
}
COMMENTS = {'line_comment', 'block_comment'}
ANNOTATIONS = {'annotation', 'marker_annotation'}
JAVA_LANG = set(('Object String StringBuilder StringBuffer Boolean Byte Short Integer Long '
                 'Float Double Character Number Void Class Enum Record Throwable Exception '
                 'RuntimeException Error Iterable Comparable CharSequence Runnable Thread '
                 'Math System Cloneable AutoCloseable').split())
DECLARATIONS = {'class_declaration', 'interface_declaration', 'enum_declaration',
                'record_declaration', 'annotation_type_declaration'}


class GsetterError(Exception):
    """Entrada que não pode ser alterada com segurança."""


def text(node):
    return node.text.decode('utf-8')


def tokens(node):
    """Tokens de tipos/assinaturas; omite anotações, comentários e argumentos genéricos."""
    if node is None or node.type in COMMENTS | ANNOTATIONS | {'type_arguments', 'modifiers'}:
        return ''
    if not node.children:
        return text(node)
    return ''.join(tokens(child) for child in node.children)


def find_file(target, root):
    candidate = Path(target)
    if candidate.is_absolute() or '/' in target or '\\' in target:
        path = root / candidate
        if path.suffix != '.java' or not path.is_file() or path.is_symlink():
            raise GsetterError(f'Ficheiro Java inexistente ou não regular: {target}')
        return path
    name = target if target.endswith('.java') else target + '.java'
    matches = []
    for directory, folders, files in os.walk(root, onerror=_walk_error):
        folders[:] = sorted(folder for folder in folders
                            if folder.lower() not in IGNORED
                            and not (Path(directory) / folder).is_symlink())
        if name in files:
            path = Path(directory) / name
            if not path.is_symlink():
                matches.append(path)
    if not matches:
        raise GsetterError(f'Classe não encontrada: {name}')
    if len(matches) > 1:
        paths = '\n'.join(f'  {p.relative_to(root).as_posix()}' for p in sorted(matches))
        raise GsetterError('Nome ambíguo; foram encontrados vários ficheiros:\n' + paths
                           + '\nExecute novamente com o caminho específico, por exemplo:'
                           + f'\n  python gsetter.py "{matches[0].relative_to(root).as_posix()}"')
    return matches[0]


def _walk_error(error):
    raise GsetterError(f'Não foi possível pesquisar o projeto: {error}')


def parse(source):
    tree = Parser(Language(tree_sitter_java.language())).parse(source)
    if tree.root_node.has_error:
        node = tree.root_node
        while not (node.is_error or node.is_missing):
            child = next((child for child in node.children
                          if child.has_error or child.is_missing), None)
            if child is None:
                break
            node = child
        row, column = node.start_point
        raise GsetterError(f'Erro de sintaxe ou Java não suportado na linha {row + 1}, '
                           f'coluna {column + 1}. O ficheiro não foi alterado.')
    return tree.root_node


def type_variables(declaration):
    params = declaration.child_by_field_name('type_parameters')
    result = {}
    if params:
        for param in params.named_children:
            if param.type != 'type_parameter':
                continue
            name = next(child for child in param.named_children if child.type == 'type_identifier')
            bound = next((child for child in param.named_children if child.type == 'type_bound'), None)
            bounds = [child for child in bound.named_children if child.type not in COMMENTS] if bound else []
            result[text(name)] = tokens(bounds[0]) if bounds else 'java.lang.Object'
    return result


def signature_type(type_text, imports, variables):
    """Apagamento de genéricos; varargs e arrays têm a mesma assinatura Java."""
    base = type_text.rstrip('[]')
    dimensions = type_text[len(base):]
    visited = set()
    while base in variables:
        if base in visited:
            raise GsetterError('Limites de tipos cíclicos; não é seguro gerar métodos.')
        visited.add(base)
        base = variables[base]
    first, dot, rest = base.partition('.')
    if first in imports:
        if imports[first] is None:
            raise GsetterError(f'O tipo {first} pode vir de um import estático. '
                               'Use o tipo totalmente qualificado para uma análise segura.')
        base = imports[first] + (dot + rest if dot else '')
    elif first in JAVA_LANG:
        base = 'java.lang.' + base
    return base + dimensions


def uncertain_signature(first, second):
    """Sem classpath, um nome simples pode designar um tipo qualificado equivalente."""
    for left, right in zip(first, second):
        a, b = left.rstrip('[]'), right.rstrip('[]')
        if left[len(a):] != right[len(b):]:
            return False
        if a != b and not (b.endswith('.' + a) or a.endswith('.' + b)):
            return False
    return len(first) == len(second)


def existing_signatures(body, imports, variables):
    signatures = set()
    for method in body.named_children:
        if method.type != 'method_declaration':
            continue
        method_vars = variables | type_variables(method)
        params = []
        for param in method.child_by_field_name('parameters').named_children:
            if param.type in COMMENTS | {'receiver_parameter'}:
                continue
            if param.type == 'spread_parameter':
                type_node = next(child for child in param.named_children
                                 if child.type not in COMMENTS | {'modifiers', 'variable_declarator'})
                raw_type = tokens(type_node) + '[]'
            elif param.type == 'formal_parameter':
                raw_type = tokens(param.child_by_field_name('type'))
                raw_type += tokens(param.child_by_field_name('dimensions'))
            else:
                raise GsetterError('Parâmetro de método não suportado; ficheiro não alterado.')
            params.append(signature_type(raw_type, imports, method_vars))
        signatures.add((text(method.child_by_field_name('name')), tuple(params)))
    return signatures


def indentation(source, declaration, body):
    def prefix(node):
        start = source.rfind(b'\n', 0, node.start_byte) + 1
        leading = source[start:node.start_byte]
        return leading.decode() if not leading.strip(b' \t') else None

    base = prefix(declaration) or ''
    prefixes = [prefix(child) for child in body.named_children if child.type not in COMMENTS]
    member = next((value for value in prefixes if value is not None and len(value) > len(base)), None)
    member = member or base + '    '
    unit = member[len(base):] if member.startswith(base) else '    '
    return member, unit


def generate(original, class_name):
    bom = b'\xef\xbb\xbf' if original.startswith(b'\xef\xbb\xbf') else b''
    source = original[len(bom):]
    try:
        source.decode('utf-8')
    except UnicodeDecodeError as error:
        raise GsetterError('Codificação não suportada: guarde o ficheiro em UTF-8.') from error
    # Java expande escapes Unicode antes da análise lexical, ao contrário do parser.
    if b'\\u' in source:
        raise GsetterError('Escapes Unicode Java (\\u...) impedem uma análise segura; ficheiro não alterado.')
    if b'\r' in source.replace(b'\r\n', b''):
        raise GsetterError('Quebras de linha CR isoladas não suportadas; ficheiro não alterado.')
    newline = '\r\n' if b'\r\n' in source else '\n'
    root = parse(source)
    classes = [child for child in root.named_children
               if child.type == 'class_declaration'
               and text(child.child_by_field_name('name')) == class_name]
    if len(classes) != 1:
        raise GsetterError(f'Não existe uma única classe de topo chamada {class_name} neste ficheiro.')
    declaration = classes[0]
    body = declaration.child_by_field_name('body')
    imports = {}
    for child in root.named_children:
        if child.type == 'import_declaration':
            if any(token.type == 'asterisk' for token in child.children):
                continue
            name = next((n for n in child.named_children if n.type in {'identifier', 'scoped_identifier'}), None)
            if name:
                short = text(name).rsplit('.', 1)[-1]
                if any(token.type == 'static' for token in child.children):
                    # Um import estático pode referir um tipo, campo ou método.
                    imports.setdefault(short, None)
                else:
                    imports[short] = text(name)
    package = next((child for child in root.named_children if child.type == 'package_declaration'), None)
    package_name = next((text(child) for child in package.named_children
                         if child.type in {'identifier', 'scoped_identifier'}), '') if package else ''
    for child in root.named_children:
        if child.type in DECLARATIONS:
            name = text(child.child_by_field_name('name'))
            imports[name] = (package_name + '.' if package_name else '') + name
    owner = imports[class_name]
    for child in body.named_children:
        if child.type in DECLARATIONS:
            name = text(child.child_by_field_name('name'))
            imports[name] = owner + '.' + name
    variables = type_variables(declaration)
    signatures = existing_signatures(body, imports, variables)
    indent, unit = indentation(source, declaration, body)
    methods = []
    planned = set()

    def add(name, params, lines):
        signature = (name, params)
        if signature in planned:
            raise GsetterError(f'Atributos gerariam assinaturas em conflito: {name}. Ficheiro não alterado.')
        planned.add(signature)
        if signature not in signatures:
            if any(existing_name == name and uncertain_signature(existing_params, params)
                   for existing_name, existing_params in signatures):
                raise GsetterError(f'Não é possível resolver a assinatura de {name} com segurança. '
                                   'Use imports explícitos ou tipos totalmente qualificados.')
            methods.append(newline.join(indent + line for line in lines))

    for field in body.named_children:
        if field.type != 'field_declaration':
            continue
        modifiers = next((child for child in field.children if child.type == 'modifiers'), None)
        flags = {child.type for child in modifiers.children} if modifiers else set()
        if 'static' in flags:
            continue
        type_node = field.child_by_field_name('type')
        for variable in field.children_by_field_name('declarator'):
            name = text(variable.child_by_field_name('name'))
            extra = variable.child_by_field_name('dimensions')
            java_type = text(type_node) + (text(extra) if extra else '')
            erased = signature_type(tokens(type_node) + tokens(extra), imports, variables)
            suffix = name[0].upper() + name[1:]
            getter = ('is' if erased == 'boolean' else 'get') + suffix
            add(getter, (), [f'public {java_type} {getter}() {{',
                             f'{unit}return this.{name};', '}'])
            if 'final' not in flags:
                setter = 'set' + suffix
                add(setter, (erased,), [f'public void {setter}({java_type} {name}) {{',
                                       f'{unit}this.{name} = {name};', '}'])
    if not methods:
        return original, 0
    close = body.children[-1]
    if close.type != '}':
        raise GsetterError('Não foi possível localizar o fecho da classe com segurança.')
    line_start = source.rfind(b'\n', 0, close.start_byte) + 1
    close_prefix = source[line_start:close.start_byte]
    # Inserir antes da indentação do fecho mantém todos os bytes originais.
    position = line_start if not close_prefix.strip(b' \t') else close.start_byte
    separator = newline if position and source[:position].endswith(b'\n') else newline * 2
    addition = separator + (newline * 2).join(methods) + newline
    if position == close.start_byte and close_prefix.strip(b' \t'):
        addition += (indent[:-len(unit)] if unit else '')
    updated = source[:position] + addition.encode('utf-8') + source[position:]
    parse(updated)
    return bom + updated, len(methods)


def save(path, original, updated):
    """Substitui o original através de um temporário, sem criar cópias de segurança."""
    staged = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.' + path.name + '.',
                                         suffix='.tmp', delete=False) as stream:
            staged = Path(stream.name)
            stream.write(updated)
        shutil.copystat(path, staged)
        if path.read_bytes() != original:
            raise GsetterError('O ficheiro mudou durante a execução; tente novamente.')
        os.replace(staged, path)
    finally:
        if staged is not None and staged.exists():
            staged.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Gera getters e setters públicos numa classe Java.')
    parser.add_argument('classe', help='Nome da classe ou caminho para o ficheiro .java')
    parser.add_argument('--dry-run', action='store_true', help='Mostra o diff sem escrever ficheiros')
    args = parser.parse_args(argv)
    try:
        path = find_file(args.classe, Path.cwd())
        original = path.read_bytes()
        updated, count = generate(original, path.stem)
        if not count:
            print(f'Não faltam métodos em {path}.')
        elif args.dry_run:
            diff = difflib.unified_diff(original.decode('utf-8-sig').splitlines(keepends=True),
                                        updated.decode('utf-8-sig').splitlines(keepends=True),
                                        fromfile=str(path), tofile=str(path) + ' (proposto)')
            for line in diff:
                sys.stdout.write(line)
                if not line.endswith('\n'):
                    sys.stdout.write('\n')
        else:
            save(path, original, updated)
            print(f'Gerados {count} métodos em {path}.')
        return 0
    except (GsetterError, OSError) as error:
        print(f'Erro: {error}', file=sys.stderr)
        return 1


def cli():
    """Entrada comum ao comando instalado e à execução direta do script."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    return main()


if __name__ == '__main__':
    sys.exit(cli())
