# Delta for Errors

## ADDED Requirements

### Requirement: Errors a person can act on
Every error the API answers with SHALL carry a sentence written for the person in `detail`
(or, for a form that isn't right, under the field's name). Being refused for lack of a
permission SHALL say what they can't do and that a host can change their role, and name
the permission's code for whoever is asked. Something that isn't there SHALL be said
plainly, without naming a database table. Being rate-limited SHALL say when to try again,
and the response SHALL keep its `Retry-After` header.

#### Scenario: Refused for lack of a permission
- **WHEN** a member without `session_create` starts a lunch
- **THEN** the system responds 403 with "Your role doesn't let you start a lunch. Ask a host
  to change your role. (It needs session_create.)"

#### Scenario: Something that isn't there
- **WHEN** someone opens a lunch that doesn't exist, or that they can't see
- **THEN** the system responds 404 with a sentence that starts "We couldn't find that."

#### Scenario: Too many requests
- **WHEN** someone goes over a rate limit
- **THEN** the system responds 429, says to try again and in how many seconds, and sets
  `Retry-After`
