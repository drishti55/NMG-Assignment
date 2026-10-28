import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

/** Display mode. A presentation preference only: it changes how much detail the console
 * shows, never what the backend does, allows or processes. Independent of the selected
 * ticket (which lives in the URL) and of any workflow. */
export type Mode = "user" | "developer";

const KEY = "relay.mode";
const Ctx = createContext<{ mode: Mode; isDev: boolean; setMode: (m: Mode) => void }>({
  mode: "user", isDev: false, setMode: () => {},
});

function read(): Mode {
  try {
    return localStorage.getItem(KEY) === "developer" ? "developer" : "user";
  } catch {
    return "user"; // storage blocked: default to the simple view
  }
}

export function ModeProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<Mode>(read);
  useEffect(() => {
    try {
      localStorage.setItem(KEY, mode);
    } catch {
      /* keep the preference in memory only */
    }
    document.documentElement.dataset.mode = mode;
  }, [mode]);
  return <Ctx.Provider value={{ mode, isDev: mode === "developer", setMode }}>{children}</Ctx.Provider>;
}

export const useMode = () => useContext(Ctx);
