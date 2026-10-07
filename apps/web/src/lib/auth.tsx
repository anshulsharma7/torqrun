import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, type ReactNode } from "react";

import { api, ROLE_RANK, UNAUTHORIZED_EVENT, type Me, type Role } from "./api";

const AuthContext = createContext<Me | null>(null);

/** The signed-in user. Only usable below <AuthGate>. */
export function useMe(): Me {
  const me = useContext(AuthContext);
  if (!me) throw new Error("useMe() used outside <AuthGate>");
  return me;
}

/** Whether the signed-in user has at least ``role``. UI hint only: the API enforces roles. */
export function useCan(role: Role): boolean {
  const me = useContext(AuthContext);
  return me !== null && ROLE_RANK[me.role] >= ROLE_RANK[role];
}

export const AUTH_KEY = ["auth-status"] as const;

export function useAuthStatus() {
  return useQuery({ queryKey: AUTH_KEY, queryFn: api.authStatus, staleTime: 60_000, retry: 1 });
}

/** Renders ``signedOut`` (with the setup flag) until someone signs in, then ``children``. */
export function AuthGate({
  children,
  signedOut,
  loading,
}: {
  children: ReactNode;
  signedOut: (setupRequired: boolean) => ReactNode;
  loading: ReactNode;
}) {
  const queryClient = useQueryClient();
  const status = useAuthStatus();

  useEffect(() => {
    // Any 401 from the API (session expired, signed out elsewhere): re-check, which shows the
    // sign-in page if the session really is gone, and drop cached data from the old session.
    const onUnauthorized = () => {
      void queryClient.invalidateQueries({ queryKey: AUTH_KEY });
    };
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, [queryClient]);

  if (status.isPending) return <>{loading}</>;
  const user = status.data?.user ?? null;
  if (!user) return <>{signedOut(status.data?.setup_required ?? false)}</>;
  return <AuthContext.Provider value={user}>{children}</AuthContext.Provider>;
}

/** Shows ``children`` only to users with at least ``role`` (UI hint; the API enforces it). */
export function Can({ role, children }: { role: Role; children: ReactNode }) {
  return useCan(role) ? <>{children}</> : null;
}
