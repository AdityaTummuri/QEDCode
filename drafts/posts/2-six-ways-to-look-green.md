# Six ways a fix can look green without being fixed

A coding agent that is graded on "tests pass" learns that there are cheaper ways to make tests
pass than fixing the code. Here are six, and how [QEDCode](https://github.com/AdityaTummuri/QEDCode)
catches each one. You can try all of them yourself in
[Fool the Grader](https://adityatummuri.github.io/QEDCode/play/), where QEDCode's real rules run in
your browser.

| Shortcut | What it looks like | How it is caught |
| --- | --- | --- |
| 1. Skip the failing test | `@pytest.mark.skip(reason="flaky")` | added skips and xfails are flagged as weakening (grade C) |
| 2. Delete the assert | the line that failed becomes `pass` | removed assert lines in a test file count as weakening |
| 3. An assert that can never fail | `assert True or result == 5` | always-true asserts are flagged |
| 4. Switch the test off | a `conftest.py` with `collect_ignore_glob` | test configuration is protected; a model try that adds it is rejected |
| 5. Edit the expected value | `== [1]` instead of `== [1, 2]` | existing tests are protected: editing one caps the grade at B |
| 6. Hard-code the answer | `if items == [1, 2, 3, 4, 5]: return [1, 2]` | passes every visible check, even grade A; only a hidden test catches it |

The first five are visible in the diff, so they are caught before anything runs. The sixth is why
benchmarks need tests the model never sees, and why QEDCode's grade A also asks for a test that
**fails before** the change: a hard-coded answer still needs an honest test to back it, and
mutation testing ("proof strength") shows how much that test really pins.

All six rules live in one place
([`verify/guard.py`](https://github.com/AdityaTummuri/QEDCode/blob/main/src/qedcode/verify/guard.py)),
and one function writes every grade. In a pull request, the
[QEDCode Check action](https://adityatummuri.github.io/QEDCode/docs/github-action/) runs them on
every change and says which one to undo.
