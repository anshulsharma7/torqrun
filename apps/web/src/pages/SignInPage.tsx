import { useMutation, useQueryClient } from "@tanstack/react-query";
import { LogIn, ShieldCheck } from "lucide-react";
import { useState, type FormEvent } from "react";

import { LogoMark } from "../components/Logo";
import { Button, ErrorState, Field, inputClass } from "../components/ui";
import { api } from "../lib/api";
import { AUTH_KEY } from "../lib/auth";

/** Sign-in, or (on a fresh install) creation of the first administrator. */
export function SignInPage({ setup }: { setup: boolean }) {
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [mismatch, setMismatch] = useState(false);

  const submit = useMutation({
    mutationFn: () => (setup ? api.setup({ email, password, name }) : api.login({ email, password })),
    onSuccess: async () => {
      queryClient.removeQueries({ predicate: (q) => q.queryKey[0] !== AUTH_KEY[0] });
      await queryClient.invalidateQueries({ queryKey: AUTH_KEY });
    },
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (setup && password !== confirm) {
      setMismatch(true);
      return;
    }
    setMismatch(false);
    submit.mutate();
  }

  return (
    <main className="grid min-h-screen place-items-center px-4 py-10">
      <div className="w-full max-w-sm animate-slide-up">
        <div className="mb-8 flex flex-col items-center text-center">
          <LogoMark className="size-11" />
          <h1 className="mt-5 text-xl font-semibold tracking-[-0.02em]">
            {setup ? "Welcome to Torqrun" : "Sign in to Torqrun"}
          </h1>
          <p className="mt-1.5 text-sm text-fg-muted">
            {setup ? "Create the first administrator account to get started." : "Run jobs, workflows and agents from one place."}
          </p>
        </div>
        <form onSubmit={onSubmit} className="card space-y-4 p-6">
          {setup && (
            <Field label="Your name" htmlFor="name">
              <input id="name" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} className={inputClass} />
            </Field>
          )}
          <Field label="Email" htmlFor="email">
            <input id="email" type="email" required autoComplete="username" autoFocus value={email} onChange={(e) => setEmail(e.target.value)} className={inputClass} />
          </Field>
          <Field label="Password" htmlFor="password" hint={setup ? "At least 10 characters." : undefined}>
            <input
              id="password"
              type="password"
              required
              minLength={setup ? 10 : undefined}
              autoComplete={setup ? "new-password" : "current-password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className={inputClass}
            />
          </Field>
          {setup && (
            <Field label="Confirm password" htmlFor="confirm">
              <input id="confirm" type="password" required autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} className={inputClass} />
            </Field>
          )}
          {mismatch && <ErrorState error={new Error("The passwords don't match.")} className="m-0" />}
          {submit.isError && <ErrorState error={submit.error} className="m-0" />}
          <Button type="submit" variant="primary" icon={setup ? ShieldCheck : LogIn} className="w-full" disabled={submit.isPending}>
            {submit.isPending ? "Please wait…" : setup ? "Create admin account" : "Sign in"}
          </Button>
        </form>
        {setup && (
          <p className="mt-4 text-center text-xs text-fg-subtle">
            This page is only available until the first account exists.
          </p>
        )}
      </div>
    </main>
  );
}
