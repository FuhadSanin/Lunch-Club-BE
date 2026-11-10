# Notifications Specification

## Purpose

Telling people when to pick and when food is on its way: push notifications to the
whole company, and email to the host when they need to act.

## Requirements

### Requirement: Company push notifications
The system SHALL let a signed-in device subscribe to its company's notifications, and
SHALL send each a push with a link to the lunch when a lunch starts, when it is ordered
and when it arrives. Notifications SHALL only be sent after the change commits.

#### Scenario: Lunch starts
- **WHEN** a host starts a lunch with a 12:30 PM cutoff
- **THEN** subscribed devices get "Pick your food by 12:30 PM" with a link to the lunch

#### Scenario: Lunch is ordered
- **WHEN** the last checkout of a lunch is placed
- **THEN** subscribed devices get "Lunch is ordered"

### Requirement: Emails that need the host
The system SHALL email the host when picks lock (to confirm the order) and whenever a
checkout needs them to finish it in the Swiggy app.

#### Scenario: Picks lock
- **WHEN** a lunch with picks locks
- **THEN** its host is emailed a link to check and confirm the cart
