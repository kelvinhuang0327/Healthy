# P0 Identity, Secure Session, and Person Ownership

- Healthy is a modular monolith with domain, application, infrastructure, and
  presentation layers.
- An Account is only an authentication principal. A Person is the sole
  health-data subject and is owned by exactly one Account.
- Browser authentication uses an opaque, revocable, expiring server session
  cookie. Raw session credentials are never persisted.
- GET operations are strictly read-only and never create or refresh records.
- Database structure changes only through versioned migrations.
- Product code and the engineering control plane remain separate.
- Legacy contract reference:
  `/Users/kelvin/Kelvin-WorkSpace/PersonalHealthOS` at
  `54f70112e95f1b84dc823d731f84e537db6b2337`.
