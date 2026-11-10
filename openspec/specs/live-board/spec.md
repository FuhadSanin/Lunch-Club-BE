# Live Board Specification

## Purpose

Everyone sees picks and order progress change without refreshing, through a Firestore
copy of each lunch that only the server writes.

## Requirements

### Requirement: Board updates after every change
After any change people can see (a pick, a skip, a lock, a confirmation, an order status)
commits, the system SHALL write the full lunch, as returned by `/api/lunches/<id>`, to
Firestore document `boards/<lunch id>`, tagged with the company id. A change that rolls
back MUST NOT reach the board.

#### Scenario: A teammate picks a dish
- **WHEN** a pick is saved
- **THEN** `boards/<lunch id>` shows it once the save has committed

#### Scenario: Firestore is unavailable
- **WHEN** writing the board fails
- **THEN** the change itself still succeeds and the failure is logged

### Requirement: Board access
People SHALL be able to read only boards of their own company, and nobody but the
server SHALL be able to write a board.

#### Scenario: Reading another company's board
- **WHEN** a signed-in person reads a board whose company id differs from their `org_id` claim
- **THEN** Firestore denies the read

#### Scenario: A browser writes a board
- **WHEN** any client tries to write to `boards/`
- **THEN** Firestore denies the write
