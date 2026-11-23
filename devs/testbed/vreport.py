#!/usr/bin/env python3
"""Generate the detailed results of a functional tests run, for inclusion in
its PDF report.

Usage: vreport.py <run-directory>

The run directory contains results.txt (lines <id>|<status>|<notes>),
evidence/<id>.txt and img/<id>-<step>.png. The script writes in the run
directory summary.adoc, the table of results, and details.adoc, with one
chapter per feature area of the specification and, for each test: its
specification, its status and notes, its screenshots and its evidence. The
specification and the evidence are copied in details.adoc: the report keeps the
specification of the tests as run, and rackslab-build-pdf only stages AsciiDoc
sources and images.
"""

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SPECS = ROOT / "tests" / "specs"


def areas():
    """Return the feature files of the specification, in the order of the
    master document."""
    master = (SPECS / "functional-tests.adoc").read_text()
    return re.findall(r"^include::(features/\w+\.adoc)\[", master, re.MULTILINE)


def tagged(source, tag):
    """Return the region of an AsciiDoc source between tag and end comments,
    with section levels increased by one."""
    match = re.search(
        rf"^// tag::{tag}\[\]\n(.*?)^// end::{tag}\[\]",
        source,
        re.MULTILINE | re.DOTALL,
    )
    return re.sub(r"^(=+ )", r"=\1", match[1], flags=re.MULTILINE)


# Colors of test statuses, inline passthrough supported by asciidoctor-pdf as
# the document themes define no color role.
STATUS_COLORS = {
    "PASS": "#2E7D32",
    "FAIL": "#BA2430",
    "BLOCKED": "#E65100",
    "SKIPPED": "#757575",
}


def colored(status):
    color = STATUS_COLORS.get(status, "#757575")
    return f'pass:[<font color="{color}"><strong>{status}</strong></font>]'


def link_issues(text):
    return re.sub(
        r"rackslab/slurm-quota#(\d+)",
        r"https://github.com/rackslab/slurm-quota/issues/\1[#\1]",
        text,
    )


def main():
    run = pathlib.Path(sys.argv[1]).resolve()
    results = {}
    for line in (run / "results.txt").read_text().splitlines():
        if line.strip():
            test, status, notes = line.split("|", 2)
            results[test] = (status, notes)
    images = sorted(p.name for p in (run / "img").glob("*.png"))

    out = []
    for feature in areas():
        path = SPECS / feature
        source = path.read_text()
        title = source.splitlines()[0].lstrip("= ")
        tests = re.findall(r"^// tag::(FT-[A-Z]+-\d+)\[\]", source, re.MULTILINE)
        out.append(f"== {title}\n")
        for test in tests:
            status, notes = results.get(test, ("NOT RUN", ""))
            out.append(tagged(source, test))
            out.append(f"Result:: {colored(status)}")
            if notes:
                out.append(f"Notes:: {link_issues(notes)}")
            out.append("")
            for image in images:
                if image.startswith(f"{test}-"):
                    step = image[len(test) + 1 : -len(".png")].replace("-", " ")
                    out.append(f".{test}, {step}")
                    out.append(f"image::{image}[{test} {step},width=100%]\n")
            evidence = run / "evidence" / f"{test}.txt"
            if evidence.exists():
                # Evidence is copied, as the PDF build only stages AsciiDoc
                # sources and images. The listing delimiter is longer than
                # the separator lines of the evidence.
                text = evidence.read_text().rstrip("\n")
                delimiter = "-" * 4
                while delimiter in text.splitlines():
                    delimiter += "-"
                out.append(f".{test}, evidence")
                out.append(
                    f"[source%autofit,console]\n{delimiter}\n{text}\n{delimiter}\n"
                )
    (run / "details.adoc").write_text("\n".join(out))

    # Summary table, in the order of the specification.
    order = [
        t
        for f in areas()
        for t in re.findall(
            r"^// tag::(FT-[A-Z]+-\d+)\[\]", (SPECS / f).read_text(), re.MULTILINE
        )
    ]
    counts = {
        s: sum(1 for r in results.values() if r[0] == s)
        for s in ("PASS", "FAIL", "BLOCKED", "SKIPPED")
    }
    summary = [
        f"{len(results)} tests: {counts['PASS']} PASS, {counts['FAIL']} FAIL, "
        f"{counts['BLOCKED']} BLOCKED, {counts['SKIPPED']} SKIPPED.\n",
        '[cols="2,1,6",options=header]',
        "|===",
        "|Test |Status |Notes",
    ]
    for test in order:
        if test in results:
            status, notes = results[test]
            notes = link_issues(notes).replace("|", "\\|")
            summary.append(f"|<<{test},{test}>> |{colored(status)} |{notes}")
    summary.append("|===\n")
    (run / "summary.adoc").write_text("\n".join(summary))
    print(f"{run / 'details.adoc'}: {len(results)} results")


if __name__ == "__main__":
    main()
