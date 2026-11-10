# Delta for Lunches

## MODIFIED Requirements

### Requirement: Skipping the day
The system SHALL let a person with `pick_update` skip an open lunch, which removes their
picks and lists them as skipped. Skipping SHALL make their day final, as completing an
order does: they can't pick again until the host reopens them.

#### Scenario: Skip is final
- **WHEN** a member with a pick skips the lunch
- **THEN** their picks are gone and they appear under `skipped` and `completed`
- **WHEN** they try to pick a dish or rejoin
- **THEN** the system responds 409

## ADDED Requirements

### Requirement: Completing an order
While a lunch is open, the system SHALL let a person with `pick_update` and at least one
pick complete their order. A completed person SHALL NOT add, change or remove picks, or
skip, and the board SHALL list them under `completed`.

#### Scenario: Completing an order
- **WHEN** a member with a pick completes their order
- **THEN** they appear under `completed`
- **AND** adding, changing or removing a pick, or skipping, responds 409

#### Scenario: Completing without a pick
- **WHEN** a member with no picks completes their order
- **THEN** the system responds 409

### Requirement: Reopening a person
While a lunch is open, the system SHALL let anyone with `session_update` reopen a person
who completed their order or skipped, so they can pick again, and mark someone as skipping
(`PUT /api/lunches/<id>/skip/<user id>`), which is final in the same way.

#### Scenario: Host reopens a completed order
- **WHEN** the host reopens a member who completed their order
- **THEN** the member is no longer under `completed` and can pick again

#### Scenario: Host reopens a skipped day
- **WHEN** the host reopens a member who skipped
- **THEN** the member is under neither `skipped` nor `completed` and can pick again

#### Scenario: Host marks someone as skipping
- **WHEN** the host marks a member who hasn't picked as skipping
- **THEN** the member is under `skipped` and `completed` until the host reopens them

#### Scenario: Reopening without the permission
- **WHEN** someone without `session_update` reopens another person
- **THEN** the system responds 403

### Requirement: Saving a whole cart at once
While a lunch is open, the system SHALL let a person with `pick_create` replace all their picks
in one request, optionally completing their order in the same request, and SHALL answer with
the board.

#### Scenario: Completing a cart in one request
- **WHEN** a member puts a cart of two dishes with `complete` set
- **THEN** the board has exactly those two dishes for them and lists them under `completed`

#### Scenario: Saving without completing
- **WHEN** a member puts a cart without `complete`
- **THEN** their picks are replaced and they can still change them

#### Scenario: Completing an empty cart
- **WHEN** a member puts no dishes with `complete` set
- **THEN** the system responds 409

### Requirement: Unlocking picks
The system SHALL let anyone with `session_update` unlock a locked lunch that isn't confirmed
yet (`POST /api/lunches/<id>/unlock`): its status goes back to `open`, the checkouts made at
locking are dropped, and picks can change again. If the cutoff has passed, it SHALL move 30
minutes ahead unless a new cutoff is given, and picks SHALL lock again at the new cutoff.

#### Scenario: Unlocking before ordering
- **WHEN** the host unlocks a locked lunch
- **THEN** it is `open` with no checkouts, and members can pick again

#### Scenario: Unlocking after the cutoff
- **WHEN** the host unlocks a lunch whose cutoff has passed
- **THEN** the cutoff is about 30 minutes away and members can pick

#### Scenario: Unlocking an ordered lunch
- **WHEN** the host unlocks a lunch that is open or already confirmed
- **THEN** the system responds 409
