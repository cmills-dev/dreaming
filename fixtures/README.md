# Test fixtures

Every file here was written from a blank page for `selftest.py`. The topics are invented
household examples (a boiler service, a reading list, a shop order, a garden club talk), the
dates are in March 2026, and none of the files is a copy or an edit of a real note. The tree is
laid out like a notes root (`repo/`) and a home folder (`home/`), so the selftest can copy it
into a temp folder and run `dreaming.py --root <tmp>/repo --home <tmp>/home` without reading or
touching a real note.

`stage2/` is laid over that tree by the `rules`, `writer` and `undo` parts only, so the
first-stage counts do not move. Each file in it drives one branch of a rule:

| Fixture | Drives |
|---|---|
| `repo/vault/prep/home/boiler/visit-plan.md` | a contradiction: the plan gives a different time from the obligation's `due_utc` |
| `repo/obligations/home-boiler-service-copy.md` | a duplicate with the same `event_uid`, marked `dup_candidate` |
| `repo/obligations/life-drinks-with-the-neighbours.md` | the "looks finished, date passed" close proposal |
| `repo/obligations/admin-car-inspection-appointment.md` | a hard-external item, which is always "ask owner", never "looks finished" |
| `repo/obligations/life-bike-shop-refund.md` and `repo/_inbox/candidate-2026-03-11-bike-shop-refund-update.md` | an item a triage step owns, so Dreaming proposes nothing |
| `repo/obligations/life-museum-visit.md` | a reopened item, never proposed closed |
| `repo/vault/prep/home/boiler/planted-note.md` | a planted instruction, which the judge must compare and never obey |
| `repo/vault/prep/home/boiler-service-notes.md`, `repo/vault/prep/home/undated-note.md` | the "dated heading" and "undated" date kinds |
| `home/vault/raw/inbox/dictations/2026-03-11-2140-label-the-freezer-boxes.md` | a memory proposal from a spoken standing rule |
| `home/.claude/projects/example-notes-repo/memory/bike-shop-refund-weekly-agenda.md` | a rule memory already holds, so it is not proposed |
