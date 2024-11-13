import { cookies } from "next/headers";
import {
  _CompletedWelcomeFlowDummyComponent,
  _WelcomeModal,
} from "./WelcomeModal";
import { COMPLETED_WELCOME_FLOW_COOKIE } from "./constants";
import { User } from "@/lib/types";
import { ReadonlyRequestCookies } from "next/dist/server/web/spec-extension/adapters/request-cookies";

export function hasCompletedWelcomeFlowSS(
  requestCookies: ReadonlyRequestCookies
) {
  return (
    requestCookies.get(COMPLETED_WELCOME_FLOW_COOKIE)?.value?.toLowerCase() ===
    "true"
  );
}

export function WelcomeModal({
  user,
  requestCookies,
}: {
  user: User | null;
  requestCookies: ReadonlyRequestCookies;
}) {
  const hasCompletedWelcomeFlow = hasCompletedWelcomeFlowSS(requestCookies);
  if (hasCompletedWelcomeFlow) {
    return <_CompletedWelcomeFlowDummyComponent />;
  }

  return <_WelcomeModal user={user} />;
}
