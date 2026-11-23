# Functional tests specification

This directory contains the specification of the functional tests of Slurm-quota. They check the features of the product
end-to-end on a real Slurm cluster, unlike the Python tests in `tests/unit/` and `tests/functional/`, which run with
mocks.

The specification is generic: it does not depend on a particular cluster or deployment tool. Site-specific values (user
names, accounts, partitions, GPU types, hosts, URLs) are AsciiDoc attributes used as placeholders, for example
`{user_a}`, listed with their description in the _Test environment_ chapter.

## Layout

- `functional-tests.adoc`: master document, with the test case structure, the placeholders, the global prerequisites and
  conventions, and includes of the feature chapters.
- `features/<area>.adoc`: test cases of one feature area. Tests of all interfaces (Slurm, CLI, REST API, web dashboard,
  admin commands) of a feature are in the same area.

## Test cases

Each test case has a stable identifier `FT-<AREA>-<NNN>`, never renumbered nor reused, and the fields `Feature`,
`Interface`, `Objective`, `Prerequisites`, `Steps` and `Expected results`. Each test case is enclosed in AsciiDoc tags
named after its identifier, so that a report can include it with `include::features/<area>.adoc[tag=FT-<AREA>-<NNN>]`.

To add a test case, append it to its feature area with the next free number, following the structure of existing test
cases. Use placeholders for all site-specific values, and declare new placeholders in `functional-tests.adoc`.

## Rendering

Render the generic specification, with placeholders displayed as `<name>`:

```sh
asciidoctor -a attribute-missing=warn tests/specs/functional-tests.adoc
```

Render it with the values of a site, by overriding the placeholders:

```sh
asciidoctor -a user_a=alice -a account_a=physics tests/specs/functional-tests.adoc
```

Use `asciidoctor-pdf` instead of `asciidoctor` to generate a PDF document.
