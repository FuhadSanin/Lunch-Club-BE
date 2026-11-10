# Ordering Specification

## Purpose

Turning a locked lunch into Swiggy orders: someone reviews and confirms the cart once,
each checkout is placed exactly once, and the lunch is tracked until the food arrives.
When automatic ordering is unavailable or fails, the order is finished in the Swiggy app.
"The host" is the person who started the lunch; they get the emails about it.

## Requirements

### Requirement: Confirming the reviewed cart
The system SHALL place orders only after someone with `review_create` confirms a locked
lunch, and SHALL then queue every checkout to be placed.

#### Scenario: Confirming a locked lunch
- **WHEN** someone with `review_create` confirms a locked lunch
- **THEN** its status becomes `confirmed` and each checkout is queued

#### Scenario: Confirming too early
- **WHEN** someone confirms a lunch that is still open
- **THEN** the system responds 409

#### Scenario: Confirming without the permission
- **WHEN** someone without `review_create` calls confirm
- **THEN** the system responds 403

### Requirement: Placing each order exactly once
Every checkout SHALL carry a one-time key that stays the same across retries. The system
MUST NOT place a checkout that is already placed, MUST NOT start a second attempt while
one is running, and after an interrupted attempt MUST check Swiggy for an existing order
before ordering again.

#### Scenario: The job is delivered twice
- **GIVEN** a checkout that was placed
- **WHEN** the place job runs again
- **THEN** nothing is sent to Swiggy

#### Scenario: Retry while an attempt is running
- **GIVEN** a checkout whose placing attempt started less than 10 minutes ago
- **WHEN** the place job runs again
- **THEN** it does nothing and asks to be retried later

#### Scenario: Retry after a crash
- **GIVEN** a checkout whose placing attempt started more than 10 minutes ago and never finished
- **WHEN** the place job runs again and Swiggy already has the order
- **THEN** the system records that order and does not order again

### Requirement: Handing an order to the host
When an order can't be placed automatically, the system SHALL mark it `needs_host`,
explain what to do, email the host, and let anyone with `order_update` enter the Swiggy
order number once they have checked out in the Swiggy app.

#### Scenario: No automatic ordering configured
- **WHEN** a confirmed checkout is placed with the manual provider
- **THEN** it is `needs_host` with instructions, and the host is emailed a link to the lunch

#### Scenario: Automatic ordering fails
- **WHEN** the ordering attempt raises an error
- **THEN** the checkout is `needs_host` and the host is told to check the Swiggy app first in case the order went through

#### Scenario: Entering the order number
- **WHEN** someone with `order_update` posts a Swiggy order number for a `needs_host` checkout
- **THEN** it is `placed` with that number

#### Scenario: Entering a number for an order that isn't waiting
- **WHEN** someone posts an order number for a checkout that is not `needs_host`
- **THEN** the system responds 409

### Requirement: Lunch progress and delivery
The lunch SHALL become `placed` when all its checkouts are placed and `delivered` when
all have arrived, notifying the company each time. When the ordering method can track
delivery the system SHALL check every 5 minutes for up to 3 hours; otherwise, or after
that, someone with `order_update` marks each checkout delivered.

#### Scenario: Recording delivery
- **GIVEN** a lunch whose only checkout is placed
- **WHEN** someone with `order_update` marks it delivered
- **THEN** the lunch is `delivered`

#### Scenario: Delivery tracked automatically
- **GIVEN** an ordering method that tracks delivery
- **WHEN** Swiggy reports the order delivered
- **THEN** the lunch is `delivered`

### Requirement: Logging every Swiggy call
The system SHALL log every call to the ordering method with its action, checkout,
outcome and duration, and SHALL raise an error alert when an attempt fails unexpectedly.

#### Scenario: A call fails
- **WHEN** an ordering attempt raises an error
- **THEN** a structured log line records the error type and duration
- **AND** an error is reported to Sentry
