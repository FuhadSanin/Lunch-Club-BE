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
first time they sign in after that company exists, with the Member role, which grants:
`session_read`, `pick_create`, `pick_update`, `pick_delete`, `restaurant_read` and
`member_read`.

#### Scenario: Company exists
- **GIVEN** a company for `acme.com`
- **WHEN** `new.person@acme.com` signs in for the first time
- **THEN** they belong to that company with the Member role
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

### Requirement: Roles and permissions
The system SHALL decide what each person may do by their permissions, named
`<resource>_<action>` (for example `session_create`), which they get only through roles.
A role belongs to one company and is a named set of permissions (for example "Auditor":
`session_read`, `member_read`); a person has any number of roles of their company, and may
do whatever their roles grant together. Every company has two built-in roles, Host (every
permission) and Member (the permissions above), which can't be renamed, changed or deleted.
Every company endpoint SHALL name the permission it needs and SHALL answer 403, naming that
permission, to anyone without it; a request the endpoint names no permission for SHALL
be refused. `GET /api/permissions` SHALL list every permission and what it allows,
`GET /api/company/roles` every role of the company with its permissions and how many
people have it, and `GET /api/me` and the members list SHALL show each person's roles and
the permissions those give.

People with `member_update` SHALL be able to make, rename, change and delete roles of
their own company and set the roles of its people, granting or removing only permissions
they hold themselves, and a company MUST always keep at least one person with
`member_update`. A role someone has can't be deleted. The person who sets up a company has
the Host role, and a personal party's host too.

#### Scenario: Giving a role
- **WHEN** someone with `member_update` makes an "Auditor" role with `session_read` and
  `member_read` and gives it to a member
- **THEN** that person has exactly those permissions, and no others

#### Scenario: Two roles
- **WHEN** a person has two roles
- **THEN** they may do what either grants

#### Scenario: Changing a role
- **WHEN** a role's permissions change
- **THEN** everyone who has it has the change

#### Scenario: Built-in roles
- **WHEN** someone renames, changes or deletes Host or Member
- **THEN** the system responds 409 and nothing changes

#### Scenario: Deleting a role people have
- **WHEN** someone deletes a role that people still have
- **THEN** the system responds 409 and says how many have it

#### Scenario: A taken name
- **WHEN** someone makes a role with a name the company already has, in any case
- **THEN** the system responds 409

#### Scenario: Missing permission
- **WHEN** someone calls an endpoint without the permission it needs
- **THEN** the system responds 403 and names the permission

#### Scenario: Unknown permission
- **WHEN** someone puts a permission that doesn't exist in a role
- **THEN** the system responds 400

#### Scenario: Granting what you don't have
- **WHEN** someone makes or changes a role, or gives or takes roles, so that a permission
  they don't hold themselves is granted or removed
- **THEN** the system responds 403 and nothing changes

#### Scenario: Last person who can change roles
- **WHEN** the only person with `member_update` tries to give it up, by their own roles or
  by changing or deleting the role that gives it
- **THEN** the system responds 409 and they keep it

#### Scenario: Another company's person or role
- **WHEN** someone targets a person or role in another company, or gives a person another
  company's role
- **THEN** the system responds 404 for a person or a role to change, and 400 for a role to give

### Requirement: Company addresses
The system SHALL keep every delivery address in one place: an address belongs to a company,
has its text and, when it was picked from the place suggestions, where it is (`lat` and `lng`,
both or neither). Anyone with `company_update` SHALL be able to add an address to the list
hosts choose from and take one off it; the same text in any case is the same address. An
address a lunch was delivered to SHALL stay with that lunch after it leaves the list.
`GET /api/company/settings` SHALL list the addresses on the list. A company SHALL also
record who set it up.

#### Scenario: Adding and removing an address
- **WHEN** someone with `company_update` adds "Tower B", adds "tower b", then removes it
- **THEN** the second add is refused as already saved, and after removing it the list is empty

#### Scenario: An address with a place
- **WHEN** an address is added with `lat` and `lng`
- **THEN** the settings list it with them
- **WHEN** only one of the two is given, or one is out of range
- **THEN** the system responds 400

#### Scenario: An address a lunch went to
- **GIVEN** a lunch delivered to an address
- **WHEN** that address is removed from the list
- **THEN** the list no longer shows it, the lunch still does, and adding it again puts the
  same address back

#### Scenario: Another company's address
- **WHEN** someone removes an address of another company
- **THEN** the system responds 404 and it stays

#### Scenario: Who set the company up
- **WHEN** a person sets up a company, or a personal party is made for them
- **THEN** the company records them as who created it

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
