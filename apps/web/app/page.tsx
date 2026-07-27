"use client";

import { FormEvent, useEffect, useState } from "react";
import { api, type Person, type SessionSummary } from "../lib/api";

export default function Home() {
  const [session, setSession] = useState<SessionSummary | null>(null);
  const [persons, setPersons] = useState<Person[]>([]);
  const [error, setError] = useState("");

  async function refresh() {
    try {
      const [current, rows] = await Promise.all([
        api.session(),
        api.persons(),
      ]);
      setSession(current);
      setPersons(rows);
      setError("");
    } catch {
      setSession(null);
      setPersons([]);
    }
  }

  useEffect(() => {
    Promise.all([api.session(), api.persons()])
      .then(([current, rows]) => {
        setSession(current);
        setPersons(rows);
      })
      .catch(() => {
        setSession(null);
        setPersons([]);
      });
  }, []);

  async function register(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    try {
      await api.register({
        email: String(form.get("email")),
        password: String(form.get("password")),
        display_name: String(form.get("display_name")),
      });
      formElement.reset();
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Registration failed");
    }
  }

  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    try {
      const current = await api.login({
        email: String(form.get("email")),
        password: String(form.get("password")),
      });
      setSession(current);
      formElement.reset();
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Login failed");
    }
  }

  async function createPerson(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    try {
      await api.createPerson({
        display_name: String(form.get("display_name")),
        relationship: String(form.get("relationship")),
      });
      formElement.reset();
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Person creation failed");
    }
  }

  async function logout() {
    try {
      await api.logout();
      setSession(null);
      setPersons([]);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Logout failed");
    }
  }

  return (
    <main>
      <header>
        <h1>Healthy</h1>
        <p className="lede">
          A secure foundation where accounts authenticate and every health
          context belongs to an explicitly owned Person.
        </p>
      </header>

      <p className="error" role="alert">
        {error}
      </p>

      {!session ? (
        <section className="grid" aria-label="Authentication">
          <article className="card">
            <h2>Create account</h2>
            <form onSubmit={register} data-testid="register-form">
              <label>
                Email
                <input name="email" type="email" autoComplete="email" required />
              </label>
              <label>
                Password
                <input
                  name="password"
                  type="password"
                  minLength={12}
                  autoComplete="new-password"
                  required
                />
              </label>
              <label>
                Your display name
                <input name="display_name" maxLength={120} required />
              </label>
              <button type="submit">Register securely</button>
            </form>
          </article>

          <article className="card">
            <h2>Sign in</h2>
            <form onSubmit={login} data-testid="login-form">
              <label>
                Email
                <input name="email" type="email" autoComplete="email" required />
              </label>
              <label>
                Password
                <input
                  name="password"
                  type="password"
                  autoComplete="current-password"
                  required
                />
              </label>
              <button type="submit">Sign in</button>
            </form>
          </article>
        </section>
      ) : (
        <section className="grid" aria-label="Authenticated account">
          <article className="card">
            <div className="session">
              <div>
                <span className="pill">Session active</span>
                <h2 data-testid="session-email">
                  {session.account.normalized_email}
                </h2>
              </div>
              <button className="secondary" type="button" onClick={logout}>
                Log out
              </button>
            </div>
            <ul className="persons" data-testid="person-list">
              {persons.map((person) => (
                <li
                  className="person"
                  key={person.id}
                  data-testid="person-card"
                  data-person-id={person.id}
                >
                  <span>
                    <strong>{person.display_name}</strong>
                    <br />
                    {person.relationship}
                  </span>
                  {person.is_default ? (
                    <span className="pill">Default Person</span>
                  ) : null}
                </li>
              ))}
            </ul>
          </article>

          <article className="card">
            <h2>Add a Person</h2>
            <form onSubmit={createPerson} data-testid="person-form">
              <label>
                Display name
                <input name="display_name" maxLength={120} required />
              </label>
              <label>
                Relationship
                <select name="relationship" defaultValue="family">
                  <option value="family">Family</option>
                  <option value="child">Child</option>
                  <option value="parent">Parent</option>
                  <option value="spouse">Spouse</option>
                  <option value="caregiver">Caregiver</option>
                </select>
              </label>
              <button type="submit">Create Person</button>
            </form>
          </article>
        </section>
      )}
    </main>
  );
}
