// Atributos HTML recentes que os tipos do React 18 ainda não trazem.
import "react";

declare module "react" {
  interface HTMLAttributes<T> {
    popover?: "auto" | "manual" | "hint" | "";
  }
  interface ButtonHTMLAttributes<T> {
    popovertarget?: string;
    popovertargetaction?: "toggle" | "show" | "hide";
  }
}
