import { createContext, useContext, useState, type ReactNode } from "react";

/** Developer Mode is a presentation preference only: it reveals telemetry the API already
 * returns to this operator. It never changes what the backend authorizes. */
const Ctx = createContext<{ on: boolean; toggle: () => void }>({ on: false, toggle: () => {} });

function read(): boolean {
  try {
    return localStorage.getItem("relay.devmode") === "1";
  } catch {
    return false;
  }
}

export function DevModeProvider({ children }: { children: ReactNode }) {
  const [on, setOn] = useState(read);
  const toggle = () =>
    setOn((v) => {
      try {
        localStorage.setItem("relay.devmode", v ? "0" : "1");
      } catch {
        /* storage unavailable: keep in memory */
      }
      return !v;
    });
  return <Ctx.Provider value={{ on, toggle }}>{children}</Ctx.Provider>;
}

export const useDevMode = () => useContext(Ctx);
