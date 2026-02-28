declare module 'diff' {
  // Keep it loose: upstream types are not shipped reliably in some bundler modes.
  export function structuredPatch(...args: unknown[]): any
}

