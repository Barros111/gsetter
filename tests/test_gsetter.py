import os
import subprocess
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / 'gsetter.py'


def java_tool(name):
    home = os.environ.get('JAVA_HOME')
    if home:
        executable = Path(home) / 'bin' / (name + ('.exe' if os.name == 'nt' else ''))
        if executable.is_file():
            return str(executable)
    return shutil.which(name)


class GsetterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def put(self, source, name='Pessoa.java'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(source.encode('utf-8'))
        return path

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.root,
                              capture_output=True, encoding='utf-8', timeout=15)

    def generate(self, source):
        path = self.put(source)
        result = self.run_cli('Pessoa')
        self.assertEqual(result.returncode, 0, result.stderr)
        return path.read_text(encoding='utf-8')

    def test_simple_fields_and_multiple_declarators(self):
        text = self.generate('class Pessoa {\n    private String nome;\n    int idade, altura;\n}\n')
        self.assertIn('public String getNome()', text)
        self.assertIn('return this.nome;', text)
        self.assertIn('public void setNome(String nome)', text)
        self.assertIn('this.nome = nome;', text)
        self.assertIn('getIdade()', text)
        self.assertIn('setAltura(int altura)', text)

    def test_primitive_boolean_wrapper_and_array(self):
        text = self.generate('class Pessoa { boolean ativo; Boolean pronto; boolean[] flags; }')
        self.assertIn('boolean isAtivo()', text)
        self.assertIn('Boolean getPronto()', text)
        self.assertIn('boolean[] getFlags()', text)
        self.assertNotIn('isFlags()', text)

    def test_final_and_static(self):
        text = self.generate('class Pessoa { final int id = 1; static int total; static final int MAX = 2; }')
        self.assertIn('getId()', text)
        self.assertNotIn('setId(', text)
        self.assertNotIn('getTotal(', text)
        self.assertNotIn('getMAX(', text)

    def test_generics_and_arrays_preserve_types(self):
        text = self.generate('class Pessoa<T> {\n  java.util.Map<String, java.util.List<T[]>> dados;\n  int[] a, b[];\n  String nomes[][];\n}\n')
        self.assertIn('java.util.Map<String, java.util.List<T[]>> getDados()', text)
        self.assertIn('setDados(java.util.Map<String, java.util.List<T[]>> dados)', text)
        self.assertIn('int[] getA()', text)
        self.assertIn('int[][] getB()', text)
        self.assertIn('String[][] getNomes()', text)

    def test_existing_signatures_and_overloads(self):
        existing = 'public String getNome() { return "custom"; }\n    public void setNome(int n) {}'
        text = self.generate('class Pessoa {\n    String nome;\n    ' + existing + '\n}\n')
        self.assertIn(existing, text)
        self.assertEqual(text.count('getNome()'), 1)
        self.assertIn('setNome(String nome)', text)

    def test_existing_annotated_array_varargs_and_qualified_types(self):
        text = self.generate('''import java.util.List;
class Pessoa {
    List<String> nomes;
    String[] apelidos;
    String nome;
    public void setNomes(java.util.List<String> v) {}
    public void setApelidos(final String... v) {}
    public void setNome(@Deprecated java.lang.String v) {}
}''')
        self.assertEqual(text.count('void setNomes('), 1)
        self.assertEqual(text.count('void setApelidos('), 1)
        self.assertEqual(text.count('void setNome('), 1)

    def test_inner_and_inherited_fields_are_excluded(self):
        source = '''class Base { int herdado; }
class Pessoa extends Base {
    int idade;
    class Interna { int segredo; public int getIdade() { return 0; } }
}
class Outra { int fora; }
'''
        text = self.generate(source)
        self.assertIn('public int getIdade()', text)
        self.assertNotIn('getSegredo', text)
        self.assertNotIn('getHerdado', text)
        self.assertNotIn('getFora', text)
        self.assertTrue(text.endswith('class Outra { int fora; }\n'))
        self.assertIn('class Interna { int segredo; public int getIdade() { return 0; } }', text)

    def test_repeated_execution_no_backups_or_changes(self):
        path = self.put('class Pessoa { int idade; }')
        self.assertEqual(self.run_cli('Pessoa').returncode, 0)
        generated = path.read_bytes()
        result = self.run_cli('Pessoa')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Não faltam', result.stdout)
        self.assertEqual(path.read_bytes(), generated)
        self.assertEqual(list(self.root.glob('*.bak*')), [])

    def test_ambiguity_and_explicit_path(self):
        first = self.put('class Pessoa { int a; }', 'a/Pessoa.java')
        second = self.put('class Pessoa { int b; }', 'b/Pessoa.java')
        result = self.run_cli('Pessoa')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('a/Pessoa.java', result.stderr.replace('\\', '/'))
        self.assertIn('b/Pessoa.java', result.stderr.replace('\\', '/'))
        self.assertEqual(self.run_cli('a/Pessoa.java').returncode, 0)
        self.assertIn(b'getA()', first.read_bytes())
        self.assertNotIn(b'getB()', second.read_bytes())

    def test_ignored_directories(self):
        for folder in ('.git', '.svn', '.hg', 'target', 'build', 'out', 'bin', 'node_modules', 'vendor', '.gradle', '.venv'):
            self.put('class Pessoa {}', folder + '/Pessoa.java')
        self.put('class Pessoa { int idade; }', 'src/Pessoa.java')
        result = self.run_cli('Pessoa')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_generation_does_not_create_backup_or_temporary_files(self):
        path = self.put('class Pessoa { int idade; }')
        result = self.run_cli('Pessoa')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b'getIdade()', path.read_bytes())
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_existing_backups_are_preserved_without_creating_new_ones(self):
        original = 'class Pessoa { int idade; }'
        path = self.put(original)
        self.put('previous', 'Pessoa.java.bak')
        self.assertEqual(self.run_cli('Pessoa').returncode, 0)
        self.assertEqual((self.root / 'Pessoa.java.bak').read_text(), 'previous')
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),
                         ['Pessoa.java', 'Pessoa.java.bak'])
        self.assertNotEqual(path.read_bytes(), original.encode())

    def test_dry_run_has_diff_and_no_side_effects(self):
        source = 'class Pessoa { int idade; }'
        path = self.put(source)
        result = self.run_cli('--dry-run', 'Pessoa')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('+++', result.stdout)
        self.assertIn('+    public int getIdade()', result.stdout)
        self.assertEqual(path.read_bytes(), source.encode())
        self.assertEqual(list(self.root.glob('*.bak*')), [])

    def test_syntax_errors_do_not_modify_or_backup(self):
        for source in ('class Pessoa { int idade = ; }', 'class Pessoa { int idade;',
                       'class Pessoa {} class Outra { int x = ; }'):
            with self.subTest(source=source):
                path = self.put(source)
                result = self.run_cli('Pessoa')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('sintaxe', result.stderr)
                self.assertEqual(path.read_bytes(), source.encode())
                self.assertEqual(list(self.root.glob('*.bak*')), [])

    def test_missing_file_and_missing_selected_class(self):
        self.assertNotEqual(self.run_cli('Ausente').returncode, 0)
        path = self.put('class Outra {}')
        result = self.run_cli('Pessoa')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Pessoa', result.stderr)
        self.assertEqual(path.read_bytes(), b'class Outra {}')

    def test_crlf_tabs_bom_unicode_and_original_bytes_preserved(self):
        source = '\ufeff// Olá\r\nclass Pessoa {\r\n\tString ação; // }\r\n}\r\n// fim\r\n'
        path = self.put(source)
        result = self.run_cli('Pessoa')
        self.assertEqual(result.returncode, 0, result.stderr)
        data = path.read_bytes()
        self.assertTrue(data.startswith(source.split('}\r\n// fim')[0].encode()))
        self.assertIn('\tpublic String getAção()'.encode(), data)
        self.assertIn(b'\t\treturn this.', data)
        self.assertEqual(data.count(b'\n'), data.count(b'\r\n'))
        self.assertTrue(data.endswith(b'}\r\n// fim\r\n'))

    def test_modern_java(self):
        text = self.generate('''sealed class Pessoa permits Filha {
    String nome;
    String texto = """
        } class Falsa { int falso; }
        """;
    int avaliar(Object o) {
        return switch (o) { case String s -> s.length(); default -> 0; };
    }
    record Dados(int valor) {}
}
final class Filha extends Pessoa {}
''')
        self.assertIn('getNome()', text)
        self.assertIn('getTexto()', text)
        self.assertNotIn('getFalso', text)
        self.assertNotIn('getValor', text)

    def test_unresolved_signature_is_rejected_without_changes(self):
        source = '''import java.util.*;
class Pessoa {
    List<String> nomes;
    public void setNomes(java.util.List<String> v) {}
}'''
        path = self.put(source)
        result = self.run_cli('Pessoa')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('assinatura', result.stderr)
        self.assertEqual(path.read_bytes(), source.encode())
        self.assertEqual(list(self.root.glob('*.bak*')), [])

    def test_partially_qualified_signature_is_rejected_without_changes(self):
        source = '''import java.util.*;
class Pessoa {
    Map.Entry<String, String> entry;
    void setEntry(java.util.Map.Entry<String, String> e) {}
}'''
        path = self.put(source)
        result = self.run_cli('Pessoa')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('assinatura', result.stderr)
        self.assertEqual(path.read_bytes(), source.encode())
        self.assertEqual(list(self.root.glob('*.bak*')), [])

    def test_static_type_import_shadowing_java_lang_is_rejected(self):
        source = '''import static exemplo.Outer.String;
class Pessoa {
    String nome;
    void setNome(exemplo.Outer.String s) {}
}'''
        path = self.put(source)
        result = self.run_cli('Pessoa')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('estático', result.stderr)
        self.assertEqual(path.read_bytes(), source.encode())
        self.assertEqual(list(self.root.glob('*.bak*')), [])

    def test_package_qualified_signature(self):
        text = self.generate('''package exemplo;
class Valor {}
class Pessoa {
    Valor valor;
    public void setValor(exemplo.Valor v) {}
}''')
        self.assertEqual(text.count('void setValor('), 1)

    def test_colliding_accessor_names_fail_without_changes(self):
        source = 'class Pessoa { int nome; int Nome; }'
        path = self.put(source)
        result = self.run_cli('Pessoa')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('conflito', result.stderr)
        self.assertEqual(path.read_bytes(), source.encode())
        self.assertEqual(list(self.root.glob('*.bak*')), [])

    def test_unsafe_encoding_and_unicode_escapes_are_rejected(self):
        for data in (b'class Pessoa { String a; } // \xff',
                     b'class Pessoa { int a; } // \\u007d'):
            with self.subTest(data=data):
                path = self.root / 'Pessoa.java'
                path.write_bytes(data)
                result = self.run_cli('Pessoa')
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(path.read_bytes(), data)
                self.assertEqual(list(self.root.glob('*.bak*')), [])

    def test_erased_generics_and_bounded_type_variables(self):
        text = self.generate('''import java.util.List;
class Pessoa<T extends Number> {
    List<String> nomes;
    T numero;
    Object objeto;
    public void setNomes(List<Integer> v) {}
    public void setNumero(Number v) {}
    public <V> void setObjeto(V v) {}
}''')
        self.assertEqual(text.count('void setNomes('), 1)
        self.assertEqual(text.count('void setNumero('), 1)
        self.assertEqual(text.count('void setObjeto('), 1)

    @unittest.skipUnless(java_tool('javac') and java_tool('java'), 'JDK opcional não instalado')
    def test_generated_java_compiles_and_accessors_work(self):
        self.generate('''import java.util.List;
public class Pessoa<T extends Number> {
    private String nome;
    private boolean ativo;
    private Boolean pronto;
    private final int id = 7;
    private static int total;
    private List<T[]> dados;
    private int[] a, b[];
    class Interna { int segredo; }
    public void setNome(int ignorado) {}
}
''')
        self.put('''class Verificar {
    public static void main(String[] args) {
        Pessoa<Integer> p = new Pessoa<>();
        p.setNome("Ana");
        p.setAtivo(true);
        p.setPronto(Boolean.FALSE);
        p.setA(new int[]{3});
        p.setB(new int[][]{{4}});
        p.setDados(java.util.List.<Integer[]>of(new Integer[]{5}));
        if (!p.getNome().equals("Ana") || !p.isAtivo() || p.getPronto()
                || p.getId() != 7 || p.getA()[0] != 3 || p.getB()[0][0] != 4
                || p.getDados().get(0)[0] != 5) throw new AssertionError();
        System.out.println("OK");
    }
}
''', 'Verificar.java')
        result = subprocess.run([java_tool('javac'), '-encoding', 'UTF-8', 'Pessoa.java', 'Verificar.java'],
                                cwd=self.root, capture_output=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([java_tool('java'), '-cp', '.', 'Verificar'], cwd=self.root,
                                capture_output=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'OK')


if __name__ == '__main__':
    unittest.main()
