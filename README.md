# Gsetter

Generate Java getters and setters from your terminal.

Writing getters and setters by hand is repetitive: the same method structure,
parameter declarations, and assignments appear in class after class. Gsetter
helps with that routine work by generating the missing accessors for you, so
you can spend more time implementing your application's behavior.

Install it once, open a terminal at the root of any Java project, and run:

```sh
gsetter Person
```

Gsetter finds `Person.java`, checks the methods already declared in the class,
and adds only the missing getters and setters. Running it again does not
duplicate methods.

## Installation

Requires **Python 3.10 or newer**. A JDK is not required to generate accessors.

Clone this repository, then install the tool:

```sh
git clone https://github.com/Barros111/gsetter.git
cd gsetter
python -m pip install .
```

The installation creates a `gsetter` command. Your Python scripts directory
must be on `PATH` for your terminal to find it. You do not need to copy the
script into each Java project. If you install it in a virtual environment,
activate that environment before using the command.

On Linux and macOS, your Python command may be `python3` instead of `python`.

## Usage

Open a terminal at your **Java project root**, then run:

```sh
gsetter Person
```

The search always starts in the terminal's current working directory,
regardless of where Gsetter is installed. It searches subdirectories for
`Person.java`. You can also pass `Person.java` as the argument.

### Preview changes

Use `--dry-run` to display a diff without changing any files:

```sh
gsetter Person --dry-run
```

### Choose a specific file

If multiple files share the same name, Gsetter lists their paths and makes
no changes. Run it again with the file you want:

```sh
gsetter src/main/java/com/example/Person.java
```

Absolute paths are also supported. Quote paths that contain spaces.

### Example

Given this class:

```java
class Person {
    private String name;
    private boolean active;
    private final int id = 1;
    private static int count;
}
```

Gsetter adds:

```java
public String getName() {
    return this.name;
}

public void setName(String name) {
    this.name = name;
}

public boolean isActive() {
    return this.active;
}

public void setActive(boolean active) {
    this.active = active;
}

public int getId() {
    return this.id;
}
```

The `final` field receives only a getter. The `static` field is ignored.

## Generation rules

- Generates public getters and setters for fields declared directly in the
  selected top-level class, whose name must match the file name.
- Excludes inherited fields, inner-class fields, and fields in other classes
  within the same file.
- Uses `isName()` for primitive `boolean` fields. Other types, including
  `Boolean` and `boolean[]`, use `getName()`.
- Gives `final` fields a getter only and ignores `static` fields.
- Preserves generic types, arrays, and array dimensions declared after a name.
- Generates setters returning `void`, with `this.field = field;` assignments.
- Checks existing methods by name and parameter types, accounting for generic
  erasure, type-variable bounds, annotations, explicit imports, and equivalent
  varargs/array signatures. Existing methods are kept unchanged, regardless of
  their visibility or return type.
- Capitalizes the first character of each field name without stripping prefixes:
  `active` becomes `isActive()`, and `isActive` becomes `isIsActive()`.

## File handling and limitations

Gsetter uses [Tree-sitter](https://github.com/tree-sitter/py-tree-sitter) and
its [Java grammar](https://github.com/tree-sitter/tree-sitter-java) to parse
Java syntax, including supported modern constructs such as sealed classes,
records, text blocks, and switch expressions. It does not parse Java with
regular expressions.

Methods are inserted before the selected class's closing brace. Existing
content is preserved, including UTF-8 BOM, indentation, and LF/CRLF line endings.
Without an indentation example, new methods use four spaces. For mixed line
endings, new methods use CRLF.

**No `.bak` files are created.** Saving uses a temporary file in the same
directory, which is removed at the end of the operation. Backups left by older
versions are not changed or deleted.

The recursive search skips version-control directories, dependencies, and
build output: `.git`, `.hg`, `.svn`, `.bzr`, `.gradle`, `.idea`, `.venv`,
`venv`, `node_modules`, `vendor`, `target`, `build`, `out`, `bin`, `dist`,
`__pycache__`, `.m2`, `.settings`, and `.tmp`. It does not follow symbolic
links during the search. Access errors stop the search with an error message.

Gsetter refuses to modify files when parsing fails, accessor names conflict,
or a method signature cannot be resolved safely. For example, wildcard imports
can leave simple and qualified type names ambiguous; use explicit imports or
fully qualified types to resolve this. Names imported through explicit static
imports also require fully qualified types when used as type names.

Only UTF-8 files, with or without a BOM, are supported. Standalone CR line endings
and Java Unicode escape sequences (`\u...`) are rejected conservatively,
including in comments and strings, because Java processes those escapes before
lexical analysis.

Selecting an inner class, interface, enum, or record as the target is not
supported. Parsing does not replace compilation, resolve the project classpath,
validate all Java semantics, or account for annotation processors such as Lombok.
The command's status messages are currently in Portuguese.

Exit codes: `0` for success, including when no methods are missing; `1` for
search, parsing, or write errors; and `2` for invalid command-line arguments.

## Update or uninstall

From the cloned repository, update your local copy and reinstall:

```sh
git pull
python -m pip install --upgrade .
```

To uninstall:

```sh
python -m pip uninstall gsetter
```

## Run the tests

From the tool's repository, after installing it:

```sh
python -m unittest discover -s tests -v
```

The tests cover simple fields, booleans, `final`, `static`, generics, arrays,
existing signatures, overloads, inner classes, modern Java, repeated class names,
repeated execution, dry runs, syntax errors, preserved formatting, and the
absence of backup or leftover temporary files. An additional integration test
compiles the generated Java and exercises the accessors when a JDK is available
through `JAVA_HOME` or `PATH`; otherwise, that test is skipped.

For direct script use, install `requirements.txt` and run
`python gsetter.py Person` from a directory containing the script, or pass the
script's full path. The installed `gsetter` command is the convenient option
for working across multiple Java projects.
