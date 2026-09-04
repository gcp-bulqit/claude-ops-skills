#!/usr/bin/env node
// Renumber this branch's drizzle migrations onto the end of development's.
//
// Run from anywhere inside the worktree, after `git merge origin/development`,
// whether the merge conflicted or not. Idempotent: exits 0 with nothing to do
// when the numbering is already correct.
//
// Rebuilds packages/database/migrations as *theirs* + *our new migrations,
// renumbered*: files renamed, meta/NNNN_snapshot.json renamed where present,
// and each moved entry's idx, tag and `when` rewritten. The `when` rewrite is
// load-bearing: migrate.js selects pending migrations by
// `entry.when > MAX(created_at)`, so an entry renumbered without a fresh `when`
// is silently never applied.

import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';

const MIGRATIONS = 'packages/database/migrations';
const JOURNAL = `${MIGRATIONS}/meta/_journal.json`;

const git = (...args) =>
  execFileSync('git', args, { encoding: 'utf8', maxBuffer: 1024 * 1024 * 64 });

// Probing git for paths that may not exist is routine here, so keep git's
// stderr to ourselves and report absence as null.
const gitOrNull = (...args) => {
  try {
    return execFileSync('git', args, {
      encoding: 'utf8',
      maxBuffer: 1024 * 1024 * 64,
      stdio: ['ignore', 'pipe', 'ignore'],
    });
  } catch {
    return null;
  }
};

const die = (message) => {
  console.error(`renumber-migration: ${message}`);
  process.exit(1);
};

const root = git('rev-parse', '--show-toplevel').trim();
process.chdir(root);

// --- work out which side is which ------------------------------------------

const merging = existsSync(join(git('rev-parse', '--git-dir').trim(), 'MERGE_HEAD'));

let ours;
let theirs;

if (merging) {
  ours = 'HEAD';
  theirs = 'MERGE_HEAD';
} else if (gitOrNull('rev-parse', '--verify', '--quiet', 'HEAD^2')) {
  // The merge already committed cleanly; renumber on top of it.
  ours = 'HEAD^1';
  theirs = 'HEAD^2';
} else {
  console.log('renumber-migration: no merge to reconcile, nothing to do');
  process.exit(0);
}

const readJournal = (ref) => {
  const raw = gitOrNull('show', `${ref}:${JOURNAL}`);
  if (raw === null) die(`cannot read ${JOURNAL} at ${ref}`);
  return JSON.parse(raw);
};

// While the merge is conflicted the working-tree journal is full of markers, so
// our side has to come from the ref. Once the merge is committed, HEAD already
// holds any earlier renumber — read from there, or we would redo it every run.
const ourSide = merging ? ours : 'HEAD';

const base = git('merge-base', ours, theirs).trim();
const oursJournal = readJournal(ourSide);
const theirsJournal = readJournal(theirs);
const baseTags = new Set(readJournal(base).entries.map((e) => e.tag));
const theirsTags = new Set(theirsJournal.entries.map((e) => e.tag));

// Our new migrations: added on our side, absent from theirs. Keep their order.
const newEntries = oursJournal.entries
  .filter((e) => !baseTags.has(e.tag) && !theirsTags.has(e.tag))
  .sort((a, b) => a.idx - b.idx);

if (newEntries.length === 0) {
  console.log('renumber-migration: this branch adds no migrations, nothing to do');
  process.exit(0);
}

// --- refuse anything that is not a clean renumber ---------------------------

const numOf = (tag) => tag.slice(0, 4);
const sqlPath = (tag) => `${MIGRATIONS}/${tag}.sql`;
const snapPath = (tag) => `${MIGRATIONS}/meta/${numOf(tag)}_snapshot.json`;

// Our side must only ADD migration files. Additions are safe to renumber;
// a modification, deletion or rename of a migration that already existed is
// not a renumber and must not be swept up by the checkout below.
const modifiedExisting = git(
  'diff',
  '--name-only',
  '--diff-filter=MDR',
  `${base}..${ours}`,
  '--',
  MIGRATIONS,
)
  .split('\n')
  .filter((p) => p && p !== JOURNAL);

if (modifiedExisting.length > 0) {
  die(
    `this branch changes migration files it did not add — not a clean renumber:\n  ${modifiedExisting.join('\n  ')}`,
  );
}

if (merging) {
  const conflicted = git('diff', '--name-only', '--diff-filter=U')
    .split('\n')
    .filter(Boolean);
  const outside = conflicted.filter((p) => !p.startsWith(`${MIGRATIONS}/`));
  if (outside.length > 0) {
    die(
      `conflicts outside ${MIGRATIONS} — resolve by hand or park the PR:\n  ${outside.join('\n  ')}`,
    );
  }
}

// --- capture our migrations, reset the directory to theirs ------------------

const captured = newEntries.map((entry) => ({
  entry,
  sql: gitOrNull('show', `${ourSide}:${sqlPath(entry.tag)}`),
  snapshot: gitOrNull('show', `${ourSide}:${snapPath(entry.tag)}`),
}));

for (const { entry, sql } of captured) {
  if (sql === null) die(`${sqlPath(entry.tag)} is in the journal but missing at ${ourSide}`);
}

// --- work out the target numbering ------------------------------------------

const theirsEntries = [...theirsJournal.entries].sort((a, b) => a.idx - b.idx);
const nextIdx = theirsEntries.length === 0 ? 0 : theirsEntries.at(-1).idx + 1;

// `when` must clear development's high-water mark even when the index does not
// move: migrate.js applies `entry.when > MAX(created_at)`, so an entry stamped
// earlier than a migration already applied on development never runs. Entries
// that already clear it keep their timestamp, which is what makes reruns no-ops.
let lastWhen = Math.max(0, ...theirsEntries.map((e) => e.when));

const renamed = [];
let restamped = 0;

const appended = captured.map(({ entry }, i) => {
  const idx = nextIdx + i;
  const tag = String(idx).padStart(4, '0') + entry.tag.slice(4);
  const when = entry.when > lastWhen ? entry.when : Math.max(Date.now(), lastWhen + 1);

  if (tag !== entry.tag) renamed.push(`${entry.tag} -> ${tag}`);
  if (when !== entry.when) restamped++;
  lastWhen = when;

  return { ...entry, idx, tag, when };
});

if (!merging && renamed.length === 0 && restamped === 0) {
  console.log('renumber-migration: numbering already correct, nothing to do');
  process.exit(0);
}

// --- rewrite ----------------------------------------------------------------

git('checkout', theirs, '--', MIGRATIONS);

const write = (path, contents) => {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, contents);
};

for (const [i, { sql, snapshot }] of captured.entries()) {
  write(sqlPath(appended[i].tag), sql);
  if (snapshot !== null) write(snapPath(appended[i].tag), snapshot);
}

const journal = { ...theirsJournal, entries: [...theirsEntries, ...appended] };
write(JOURNAL, `${JSON.stringify(journal, null, 2)}\n`);

// Drop our old paths that theirs does not also own.
for (const { entry } of captured) {
  for (const path of [sqlPath(entry.tag), snapPath(entry.tag)]) {
    const ownedByTheirs = gitOrNull('cat-file', '-e', `${theirs}:${path}`) !== null;
    if (!ownedByTheirs && existsSync(path)) rmSync(path);
  }
}

// --- static checks, scoped to what we moved ---------------------------------

const seenIdx = new Set();
const seenTag = new Set();
for (const e of journal.entries) {
  if (seenIdx.has(e.idx)) die(`duplicate idx ${e.idx} in journal after renumber`);
  if (seenTag.has(e.tag)) die(`duplicate tag ${e.tag} in journal after renumber`);
  seenIdx.add(e.idx);
  seenTag.add(e.tag);
}

let prev = null;
for (const e of appended) {
  if (numOf(e.tag) !== String(e.idx).padStart(4, '0')) {
    die(`entry ${e.tag} disagrees with its idx ${e.idx}`);
  }
  if (!existsSync(sqlPath(e.tag))) die(`${sqlPath(e.tag)} missing after renumber`);
  if (prev && (e.idx <= prev.idx || e.when <= prev.when)) {
    die(`appended entries are not strictly ascending at ${e.tag}`);
  }
  prev = e;
}

git('add', '--', MIGRATIONS);

const report = [
  renamed.length > 0 ? renamed.join(', ') : null,
  restamped > 0 ? `${restamped} entr${restamped === 1 ? 'y' : 'ies'} restamped` : null,
].filter(Boolean);

console.log(`renumber-migration: ${report.length > 0 ? report.join('; ') : 'conflict resolved, numbering unchanged'}`);
