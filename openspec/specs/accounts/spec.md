# Accounts Specification

## Purpose

Who people are and which company they belong to: Google sign-in through Firebase,
companies matched by work email domain, and the permissions that decide what each
person may do.

## Requirements

### Requirement: Google sign-in on every request
The system SHALL authenticate every API request with a Firebase ID token sent as
`Authorization: Bearer <token>`, and SHALL accept only accounts with a verified email.

#### Scenario: No token
- **WHEN** a request arrives without a token
- **THEN** the system responds 401 with a `WWW-Authenticate: Bearer` challenge

#### Scenario: Unverified email
- **WHEN** a token's email is not verified
- **THEN** the system rejects the request and asks the person to sign in with a verified Google account

#### Scenario: Repeat sign-in
- **WHEN** the same Firebase account signs in again
- **THEN** the system finds the existing person and creates no duplicate

### Requirement: Joining a company by email domain
The system SHALL place a person in the company whose email domain matches theirs the
first time they sign in after that company exists, with the member permissions:
`session_read`, `pick_create`, `pick_update`, `pick_delete`, `restaurant_read` and
`member_read`.

#### Scenario: Company exists
- **GIVEN** a company for `acme.com`
- **WHEN** `new.person@acme.com` signs in for the first time
- **THEN** they belong to that company with the member permissions
- **AND** their email is stored in lower case

#### Scenario: No company yet
- **WHEN** someone whose domain has no company signs in
- **THEN** `GET /api/me` returns them with `org: null`
- **AND** every company endpoint responds 403 until they set up or join a company

### Requirement: Setting up a company
The system SHALL let a signed-in person without a company create one for their email
domain, whatever the domain (gmail.com included), and SHALL give them every permission in
it. Only people with the same email domain SHALL join it.

#### Scenario: Work domain
- **WHEN** `founder@startup.io` posts `{"name": "Startup"}` to `/api/company`
- **THEN** a company for `startup.io` exists and the founder has every permission
- **AND** teammates from `startup.io` join it when they next sign in

#### Scenario: Personal domain
- **WHEN** `founder@gmail.com` sets up a company
- **THEN** a company for `gmail.com` exists, and anyone with a gmail.com address joins it

#### Scenario: Another domain
- **GIVEN** a company for `acme.com`
- **WHEN** `member@gmail.com` signs in
- **THEN** they don't join it

### Requirement: Permissions
The system SHALL decide what each person may do by their permissions, named
`<resource>_<action>` (for example `session_create`), and never by a role. Every company
endpoint SHALL name the permission it needs and SHALL answer 403, naming that
permission, to anyone without it; a request the endpoint names no permission for SHALL
be refused. `GET /api/permissions` SHALL list every permission and what it allows.

People with `member_update` SHALL be able to set the permissions of people in their own
company, granting or removing only permissions they hold themselves, and a company MUST
always keep at least one person with `member_update`.

#### Scenario: Granting a permission
- **WHEN** someone with `member_update` grants `session_create` to a member
- **THEN** that person can start lunches

#### Scenario: Missing permission
- **WHEN** someone calls an endpoint without the permission it needs
- **THEN** the system responds 403 and names the permission

#### Scenario: Unknown permission
- **WHEN** someone grants a permission that doesn't exist
- **THEN** the system responds 400

#### Scenario: Granting what you don't have
- **WHEN** someone grants or removes a permission they don't hold themselves
- **THEN** the system responds 403 and nothing changes

#### Scenario: Last person who can change permissions
- **WHEN** the only person with `member_update` tries to give it up
- **THEN** the system responds 409 and they keep it

#### Scenario: Another company's person
- **WHEN** someone targets a person in another company
- **THEN** the system responds 404

### Requirement: Company isolation
Each company SHALL see only its own lunches, picks and orders. The database MUST enforce
this as well as the API, so a query that forgets to filter still returns nothing from
another company, and with no company selected it returns nothing at all.

#### Scenario: Looking at another company's lunch
- **WHEN** a person opens or picks in a lunch of another company
- **THEN** the system responds 404

#### Scenario: Unfiltered database query
- **GIVEN** lunches in two companies
- **WHEN** the database is queried for all lunches while scoped to one company
- **THEN** only that company's lunches are returned
- **AND** with no company scoped, none are returned
