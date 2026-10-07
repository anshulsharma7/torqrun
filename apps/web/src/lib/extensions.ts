// Optional Team/Enterprise UI. src/ee exists only in the private repository; in the open-source
// build this glob matches nothing and the app is the Community Edition.
import type { LucideIcon } from "lucide-react";
import type { ReactElement } from "react";

import type { PaidFeature } from "./edition";

export interface ExtensionPage {
  feature: PaidFeature;
  path: string;
  label: string;
  icon: LucideIcon;
  element: ReactElement;
}

export interface Extension {
  pages: ExtensionPage[];
}

const modules = import.meta.glob<{ extension: Extension }>("../ee/index.tsx", { eager: true });

export const extension: Extension = Object.values(modules)[0]?.extension ?? { pages: [] };
