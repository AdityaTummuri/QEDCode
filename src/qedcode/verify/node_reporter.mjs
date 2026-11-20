// A test reporter for `node --test` (AGENTS.md section 15.3). Part of QEDCode, never written by a model.
// It writes one JSON document: every test with its file, its ancestors' names, its status and the
// failure message. Suites are not listed (their children are). A test file that cannot even load is
// reported as one failed test named after the file.
import { basename, resolve } from 'node:path';

export default async function* reporter(source) {
  const results = [];
  const open = new Map(); // file -> names of the suites currently running (index = nesting)
  const stderr = new Map(); // file -> what the test file wrote to stderr (why a file did not load)
  for await (const event of source) {
    const d = event.data;
    if (event.type === 'test:start') {
      const stack = open.get(d.file) || [];
      stack.length = d.nesting;
      stack[d.nesting] = d.name;
      open.set(d.file, stack);
      continue;
    }
    if (event.type === 'test:stderr' && d.file) {
      const key = resolve(d.file); // stderr events carry the path as it was given, others absolute
      stderr.set(key, ((stderr.get(key) || '') + d.message).slice(-4000));
      continue;
    }
    if (event.type !== 'test:pass' && event.type !== 'test:fail') continue;
    if (d.details && d.details.type === 'suite') continue;
    const stack = open.get(d.file) || [];
    const err = event.type === 'test:fail' && d.details ? d.details.error : null;
    const cause = err && err.cause && typeof err.cause === 'object' ? err.cause : null;
    const wholeFile = !!d.file && d.nesting === 0 && d.name === basename(d.file);
    const why = wholeFile && stderr.get(resolve(d.file)) ? stderr.get(resolve(d.file)) : '';
    results.push({
      file: d.file || null,
      name: d.name,
      path: stack.slice(0, d.nesting),
      wholeFile,
      status: event.type === 'test:pass' ? (d.skip ? 'skipped' : d.todo ? 'todo' : 'passed') : 'failed',
      message: err ? String(why || (cause && cause.message) || err.message || '') : '',
      stack: err ? String(why || (cause && cause.stack) || err.stack || '') : '',
    });
  }
  yield JSON.stringify({ results }) + '\n';
}
