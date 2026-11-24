# T12 — Historical runtime telemetry recovery

T12 adds to T1–T11 and modifies none of their files or workflows.

## Scope

T12 searches existing historical records for evidence that could tell apart the
sequences T9–T11 showed can reproduce the metadata of
`/mnt/data/gh_demo/screenshots/01_github.com_01.png`. It runs no new extraction
experiments.

## Method

Work proceeded in small, audited stages:
1. Verify the repository and branch.
2. Inventory the sources.
3. Search for timestamps, directory metadata, process provenance and filesystem
   provenance.
4. Update the candidate matrix.
5. Write the report.
6. Confirm that no T1–T11 file changed.

Historical records that this sandbox cannot reach were read through one
read-only Actions job:
- the Azure blob storage behind artifact downloads is blocked by the sandbox
  proxy (HTTP 403);
- the job is `.github/workflows/archive-boundary-t12.yml`, running
  `t12_collect_existing_evidence.py` with `contents: read` and `actions: read`;
- run 45327222852 produced evidence artifact
  `archive-boundary-t12-existing-evidence-45327222852` (ID 14890327033).

## Files

| File | Content |
|---|---|
| `evidence_inventory.json` | every examined source and its availability, with exact failures |
| `search_log.json` | the queries, locations and outcomes |
| `recovered_evidence.json` | only recovered historical facts, with source references and confidence; user-provided values are kept separate |
| `candidate_matrix.json` | candidate sequences, the source-archive dimension and the eliminated variants |
| `report.md` | full findings and answers to the nine questions |
| `t12_collect_existing_evidence.py` | the read-only collector used by the T12 workflow |

## Key results

- No telemetry from the historical `/mnt/data` runtime is reachable: no mtime,
  ctime or birth time, no `screenshots/` or `gh_demo/` metadata, no process
  lineage and no mount data.
- The upstream origin of the bytes is recovered. The PNG is the original
  Playwright capture of the GHSA-764m-3ppc-5m73 page. The artifacts' DOS
  timestamps record runner-side mtimes in UTC: the PNG was written at
  08:07:56–08:07:58Z in run 45136043351.
- A second retained artifact, 14797203454 (run 45130379294), contains a
  byte-identical PNG with DOS time 07:21:58. The content hash therefore does not
  uniquely identify artifact 14799922102 as the source.
- Final assessment: **INSUFFICIENT HISTORICAL EVIDENCE**. The four mechanism
  families remain CONSISTENT.

## Limitations

- GitHub code search covers the default branch only.
- The uniqueness check covers only retained artifacts.
- Gmail and Google Drive were not searched, because they are not in the
  authorised source list.
- The historical platform runtime is inaccessible.
- User-provided historical values are used as inputs and were not re-verified.
