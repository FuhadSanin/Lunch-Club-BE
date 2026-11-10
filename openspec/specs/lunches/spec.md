# Lunches Specification

## Purpose

A group lunch from start to locked cart: someone opens it with a cutoff (they are its
host), everyone picks dishes from any restaurant or skips, and at the cutoff the picks
become Swiggy checkouts. Each action needs the permission named below.

## Requirements

### Requirement: Starting a lunch
The system SHALL let people with `session_create` start a lunch with a delivery address
and a cutoff time within the next 24 hours, and SHALL tell the company it has started.

#### Scenario: Starting a lunch
- **WHEN** someone with `session_create` posts a cutoff and delivery address to `/api/lunches`
- **THEN** the system responds 201 with the open lunch
- **AND** the company gets a push notification with the cutoff time and a link to the lunch

#### Scenario: Starting without the permission
- **WHEN** someone without `session_create` posts to `/api/lunches`
- **THEN** the system responds 403

#### Scenario: Cutoff out of range
- **WHEN** the cutoff is in the past or more than 24 hours away
- **THEN** the system responds 400

### Requirement: Joining from a link
The system SHALL show anyone in the company with `session_read` the full lunch at
`/api/lunches/<id>`: the host, cutoff, status, every person's picks, who skipped, the
Swiggy orders and the total.

#### Scenario: Teammate opens the link
- **WHEN** a member of the company requests the lunch
- **THEN** they see every pick with the name of the person who made it and the total in paise

### Requirement: Picking dishes
While a lunch is open and before its cutoff, the system SHALL let people with
`pick_create` add dishes (restaurant, dish, quantity 1 to 20, price in paise, optional
note up to 200 characters), and with `pick_update` and `pick_delete` change or remove
them. Nobody SHALL change or remove someone else's picks.

#### Scenario: Picking a dish
- **WHEN** a member adds 2 × a ₹320 dish
- **THEN** the lunch total grows by 64,000 paise

#### Scenario: Changing someone else's pick
- **WHEN** a person edits or deletes another person's pick
- **THEN** the system responds 404

#### Scenario: Picking after the cutoff
- **WHEN** someone adds a pick after the cutoff has passed
- **THEN** the system responds 409 and the pick is not saved

### Requirement: Skipping the day
The system SHALL let a person with `pick_update` skip an open lunch, which removes their
picks and lists them as skipped; picking a dish afterwards SHALL rejoin them.

#### Scenario: Skip then rejoin
- **WHEN** a member with a pick skips the lunch
- **THEN** their picks are gone and they appear under `skipped`
- **WHEN** they pick a dish again
- **THEN** they no longer appear under `skipped`

### Requirement: Locking picks at the cutoff
The system SHALL lock picks automatically at the cutoff, and SHALL let anyone with
`session_update` lock them earlier. Locking SHALL group picks into Swiggy checkouts of at
most 5 restaurants each, record each checkout's restaurant count and total, and email the
host to confirm. Locking a lunch nobody picked for SHALL cancel it.

#### Scenario: Seven restaurants
- **GIVEN** picks from 7 different restaurants
- **WHEN** the lunch locks
- **THEN** it has two Swiggy checkouts, of 5 and 2 restaurants, with their totals
- **AND** every pick belongs to one of them

#### Scenario: Cutoff job arrives early
- **WHEN** the scheduled lock runs before the cutoff
- **THEN** it does nothing and is retried later

#### Scenario: Nobody picked
- **WHEN** a lunch with no picks locks
- **THEN** it is cancelled

#### Scenario: Locking without the permission
- **WHEN** someone without `session_update` tries to lock the lunch
- **THEN** the system responds 403

### Requirement: Cancelling a lunch
The system SHALL let anyone with `session_delete` cancel a lunch that is open or
locked, and SHALL refuse once it has been confirmed.

#### Scenario: Cancelling an open lunch
- **WHEN** someone with `session_delete` cancels an open lunch
- **THEN** its status becomes `cancelled`
