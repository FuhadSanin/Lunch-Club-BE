# Live Board Specification

## Purpose

Everyone sees picks and order progress change without refreshing: a small Firestore
document per lunch, which only the server writes, tells browsers that the lunch changed,
and they fetch the board from the API.

## Requirements

### Requirement: Board updates after every change
After any change people can see (a pick, a skip, a lock, a confirmation, an order status)
commits, the system SHALL update Firestore document `boards/<lunch id>` with the lunch's
status, the time of the change, the company id and who else may listen. It MUST NOT carry
what is in the lunch (picks, names, emails): that is read from `/api/lunches/<id>` only.
A change that rolls back MUST NOT reach the board.

#### Scenario: A teammate picks a dish
- **WHEN** a pick is saved
- **THEN** `boards/<lunch id>` changes once the save has committed, and holds no picks

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
