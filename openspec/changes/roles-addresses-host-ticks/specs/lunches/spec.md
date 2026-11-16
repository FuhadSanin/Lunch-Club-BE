# Delta for Lunches

## MODIFIED Requirements

### Requirement: Starting a lunch
The system SHALL let people with `session_create` start a lunch with a delivery address
and a cutoff time within the next 24 hours, and SHALL tell the company it has started.
The delivery address is kept in the company's addresses (one table for every address, with
where it is when it came from the place suggestions, and whether it is on the host's
list) and the lunch points at it; an address with the same text, in any case, is reused.

#### Scenario: Starting a lunch
- **WHEN** someone with `session_create` posts a cutoff and an `address` (its text, and
  `lat` and `lng` together, if known) to `/api/lunches`
- **THEN** the system responds 201 with the open lunch
- **AND** the company gets a push notification with the cutoff time and a link to the lunch

#### Scenario: Reusing an address
- **GIVEN** a company address "Tower A"
- **WHEN** a lunch is started for "tower a"
- **THEN** the lunch points at the existing address and no second one is made

#### Scenario: An address that needs both coordinates or neither
- **WHEN** a lunch is started with a `lat` and no `lng`
- **THEN** the system responds 400

#### Scenario: Starting without the permission
- **WHEN** someone without `session_create` posts to `/api/lunches`
- **THEN** the system responds 403

#### Scenario: Cutoff out of range
- **WHEN** the cutoff is in the past or more than 24 hours away
- **THEN** the system responds 400

### Requirement: Skipping the day
The system SHALL let a person with `pick_update` skip an open lunch, which removes their
picks and completes their order with nothing in it. Those people are listed as `skipped`:
there is no separate record of skipping, so skipped means completed with no dishes. Only
the host can reopen them to pick again.

#### Scenario: Skip then rejoin
- **WHEN** a member with a pick skips the lunch
- **THEN** their picks are gone and they appear under `skipped` and `completed`
- **WHEN** the host reopens them and they pick a dish
- **THEN** they no longer appear under `skipped`

## ADDED Requirements

### Requirement: One lunch at a time
The system SHALL let a company run one lunch at a time: starting another while one is
neither called off nor closed by its host SHALL be refused, whoever asks. A person's
party is held to the same rule.

#### Scenario: A second lunch while one is running
- **GIVEN** a company's lunch that isn't over
- **WHEN** any of its hosts starts another
- **THEN** the system responds 409 and names the lunch that is running

#### Scenario: Starting again
- **WHEN** the running lunch is called off, or closed after its food arrived
- **THEN** a new lunch can be started

### Requirement: The host's ticks
The system SHALL keep, on the lunch, what its host ticks while running it: restaurants and
people reviewed, restaurants whose food has arrived, and people who have their food. Anyone
with `session_update` in the lunch's company SHALL be able to tick and untick them, and the
board SHALL show them to everyone in the lunch on every device. Only a restaurant someone
picked from, or a person with dishes, can be ticked. When someone's picks change, the review
ticks for them and for the restaurants in their old and new picks SHALL be cleared.

#### Scenario: Ticks on every device
- **WHEN** a host ticks a restaurant as arrived
- **THEN** the board shows it for everyone, and another host can untick it

#### Scenario: Ticking what isn't in the lunch
- **WHEN** someone ticks a restaurant nobody picked from, or a person with no dishes
- **THEN** the system responds 400

#### Scenario: A changed pick is reviewed again
- **GIVEN** a person and their restaurant ticked as reviewed
- **WHEN** that person changes their pick
- **THEN** both ticks are cleared, and other people's stay
