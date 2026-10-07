import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Server, Terminal, Trash2 } from "lucide-react";
import { useSearchParams } from "react-router";

import { AgentCard } from "../components/AgentCard";
import { ConnectAgentDialog } from "../components/ConnectAgentDialog";
import { Badge, Button, Card, EmptyState, ErrorState, Loading, PageHeader, Table } from "../components/ui";
import { api } from "../lib/api";
import { useCan } from "../lib/auth";
import { formatRelative } from "../lib/format";

function TokensCard() {
  const queryClient = useQueryClient();
  const tokens = useQuery({ queryKey: ["enrollment-tokens"], queryFn: api.listEnrollmentTokens, refetchInterval: 10000 });
  const revoke = useMutation({
    mutationFn: api.revokeEnrollmentToken,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["enrollment-tokens"] }),
  });
  if (!tokens.data?.length) return null;
  return (
    <Card title="Active enrollment tokens" icon={KeyRound} className="mt-6">
      <Table head={["Token", "For", "Queues", "Uses", "Expires", ""]}>
        {tokens.data.map((t) => (
          <tr key={t.id}>
            <td className="px-5 py-3 font-mono text-xs">{t.prefix}…</td>
            <td className="px-5 py-3">{t.description || <span className="text-fg-subtle">—</span>}</td>
            <td className="px-5 py-3">{t.queues.length ? t.queues.map((q) => <Badge key={q}>{q}</Badge>) : <span className="text-xs text-fg-subtle">agent decides</span>}</td>
            <td className="px-5 py-3 tabular-nums text-fg-muted">{t.uses}/{t.max_uses ?? "∞"}</td>
            <td className="px-5 py-3 text-xs text-fg-muted">{formatRelative(t.expires_at)}</td>
            <td className="px-5 py-3 text-right">
              <Button size="sm" icon={Trash2} onClick={() => revoke.mutate(t.id)} disabled={revoke.isPending}>
                Revoke
              </Button>
            </td>
          </tr>
        ))}
      </Table>
    </Card>
  );
}

export function FleetPage() {
  const [params, setParams] = useSearchParams();
  const connectOpen = params.get("connect") === "1";
  const setConnect = (open: boolean) => setParams(open ? { connect: "1" } : {}, { replace: true });
  const agents = useQuery({ queryKey: ["agents"], queryFn: api.listAgents, refetchInterval: 4000 });
  const isAdmin = useCan("admin");

  const activeAgents = agents.data?.filter((a) => a.status !== "REVOKED") ?? [];
  const online = activeAgents.filter((a) => a.connected).length;
  const slots = agents.data?.reduce((n, a) => n + (a.connected ? a.max_slots : 0), 0) ?? 0;
  const busy = agents.data?.reduce((n, a) => n + a.running, 0) ?? 0;

  return (
    <>
      <PageHeader
        icon={Server}
        title="Fleet"
        subtitle={
          activeAgents.length
            ? `${online} of ${activeAgents.length} agents online · ${busy}/${slots} job slots in use`
            : "Machines that run your jobs."
        }
        actions={
          isAdmin && (
            <Button variant="primary" icon={Terminal} onClick={() => setConnect(true)}>
              Connect agent
            </Button>
          )
        }
      />
      {agents.isPending ? (
        <Card>
          <Loading />
        </Card>
      ) : agents.isError ? (
        <Card>
          <ErrorState error={agents.error} />
        </Card>
      ) : agents.data.length === 0 ? (
        <Card>
          <EmptyState
            icon={Server}
            title="No agents yet"
            action={
              isAdmin && (
                <Button variant="primary" icon={Terminal} onClick={() => setConnect(true)}>
                  Connect your first agent
                </Button>
              )
            }
          >
            Agents are small processes on your laptop, servers or cloud VMs. They connect out to Torqrun and run
            the jobs you send them.
          </EmptyState>
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 2xl:grid-cols-3">
          {[...agents.data]
            .sort((x, y) => Number(x.status === "REVOKED") - Number(y.status === "REVOKED"))
            .map((a) => (
              <AgentCard key={a.id} agent={a} />
            ))}
        </div>
      )}
      {isAdmin && <TokensCard />}
      {isAdmin && <ConnectAgentDialog open={connectOpen} onClose={() => setConnect(false)} />}
    </>
  );
}
