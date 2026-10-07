import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Plus, Trash2, UserRound } from "lucide-react";
import { useState, type FormEvent } from "react";

import { Badge, Button, Card, CodeBlock, EmptyState, ErrorState, Field, inputClass, Loading, PageHeader, Table } from "../components/ui";
import { api } from "../lib/api";
import { useMe } from "../lib/auth";
import { formatRelative } from "../lib/format";
import { ROLE_HELP } from "../lib/edition";

function PasswordForm() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const change = useMutation({
    mutationFn: () => api.changePassword({ current_password: current, new_password: next }),
    onSuccess: () => {
      setCurrent("");
      setNext("");
    },
  });
  function onSubmit(e: FormEvent) {
    e.preventDefault();
    change.mutate();
  }
  return (
    <Card title="Change password" icon={KeyRound} bodyClassName="p-5">
      <form onSubmit={onSubmit} className="space-y-4">
        <Field label="Current password" htmlFor="current-password">
          <input id="current-password" type="password" required autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} className={inputClass} />
        </Field>
        <Field label="New password" htmlFor="new-password" hint="At least 10 characters. Your other sessions are signed out.">
          <input id="new-password" type="password" required minLength={10} autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} className={inputClass} />
        </Field>
        {change.isError && <ErrorState error={change.error} className="m-0" />}
        {change.isSuccess && <p className="text-sm text-ok">Password changed.</p>}
        <Button type="submit" disabled={change.isPending}>Change password</Button>
      </form>
    </Card>
  );
}

function ApiTokens() {
  const queryClient = useQueryClient();
  const tokens = useQuery({ queryKey: ["api-tokens"], queryFn: api.listTokens });
  const [name, setName] = useState("");
  const [days, setDays] = useState("90");
  const create = useMutation({
    mutationFn: () => api.createToken({ name, expires_in_days: days === "" ? null : Number(days) }),
    onSuccess: async () => {
      setName("");
      await queryClient.invalidateQueries({ queryKey: ["api-tokens"] });
    },
  });
  const revoke = useMutation({
    mutationFn: api.revokeToken,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["api-tokens"] }),
  });
  const origin = window.location.origin;

  return (
    <Card title="API tokens" icon={KeyRound} bodyClassName="flex flex-col">
      <form
        className="flex flex-wrap items-end gap-3 border-b border-line p-5"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <div className="min-w-48 flex-1">
          <Field label="Token name" htmlFor="token-name">
            <input id="token-name" required placeholder="ci-pipeline" value={name} onChange={(e) => setName(e.target.value)} className={inputClass} />
          </Field>
        </div>
        <div className="w-36">
          <Field label="Expires" htmlFor="token-days">
            <select id="token-days" value={days} onChange={(e) => setDays(e.target.value)} className={inputClass}>
              <option value="7">in 7 days</option>
              <option value="30">in 30 days</option>
              <option value="90">in 90 days</option>
              <option value="365">in 1 year</option>
              <option value="">never</option>
            </select>
          </Field>
        </div>
        <Button type="submit" icon={Plus} disabled={create.isPending}>Create token</Button>
      </form>
      {create.data && (
        <div className="space-y-2 border-b border-line bg-ok/5 p-5">
          <p className="text-sm font-medium">Copy your new token now: it won't be shown again.</p>
          <CodeBlock code={create.data.token} label="Token" />
          <CodeBlock code={`curl -H "Authorization: Bearer ${create.data.token}" ${origin}/api/v1/jobs`} label="Example" />
        </div>
      )}
      {(create.error ?? revoke.error) && <ErrorState error={create.error ?? revoke.error} />}
      {tokens.isPending ? (
        <Loading rows={2} />
      ) : tokens.isError ? (
        <ErrorState error={tokens.error} />
      ) : tokens.data.length === 0 ? (
        <EmptyState title="No API tokens">Tokens let scripts and CI call the API with your role.</EmptyState>
      ) : (
        <Table head={["Name", "Token", "Last used", "Expires", ""]}>
          {tokens.data.map((t) => (
            <tr key={t.id} className="transition hover:bg-surface-2/50">
              <td className="px-5 py-3 font-medium">{t.name}</td>
              <td className="px-5 py-3 font-mono text-xs text-fg-muted">{t.prefix}…</td>
              <td className="px-5 py-3 text-xs text-fg-muted">{t.last_used_at ? formatRelative(t.last_used_at) : "never"}</td>
              <td className="px-5 py-3 text-xs text-fg-muted">{t.expires_at ? formatRelative(t.expires_at) : "never"}</td>
              <td className="px-5 py-3 text-right">
                <Button size="sm" variant="ghost" icon={Trash2} onClick={() => revoke.mutate(t.id)} disabled={revoke.isPending}>
                  Revoke
                </Button>
              </td>
            </tr>
          ))}
        </Table>
      )}
    </Card>
  );
}

export function AccountPage() {
  const me = useMe();
  return (
    <>
      <PageHeader
        icon={UserRound}
        title={me.name}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            {me.email} <Badge>{me.role}</Badge> <span className="text-fg-subtle">{ROLE_HELP[me.role]}</span>
          </span>
        }
      />
      <div className="grid grid-cols-1 items-start gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
        <ApiTokens />
        <PasswordForm />
      </div>
    </>
  );
}
