# Delta for Live Board

## MODIFIED Requirements

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
