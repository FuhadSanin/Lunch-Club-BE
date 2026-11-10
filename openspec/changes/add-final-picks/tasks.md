# Tasks

## 1. Final picks
- [x] 1.1 `OrderSession.completed_by` and migration with row-level security
- [x] 1.2 `services.complete` and `services.reopen`; picks, changes, skips and unskips refuse a completed person
- [x] 1.3 `PUT /api/lunches/<id>/complete` (`pick_update`) and `DELETE /api/lunches/<id>/complete/<user id>` (`session_update`)
- [x] 1.4 `completed` on the board
- [x] 1.5 `PUT /api/lunches/<id>/picks` (`pick_create`): replace your picks and optionally complete them in one request; skip and unskip answer with the board
- [x] 1.6 `POST /api/lunches/<id>/unlock` (`session_update`): reopen locked picks before ordering
- Verified by: `tests/test_lunches.py` (unlocking, completing, completing without a pick, final skip, reopening, permission, whole cart in one request)
