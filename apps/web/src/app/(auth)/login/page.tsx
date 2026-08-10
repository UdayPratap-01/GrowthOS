"use client";

import Link from "next/link";
import { FormEvent, Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { api, setTokens } from "@/lib/api";

// Demo credentials are never hardcoded. They come from env vars that are only
// defined in local development, so a production build has nothing to reveal.
const DEMO_EMAIL = process.env.NEXT_PUBLIC_DEMO_EMAIL ?? "";
const DEMO_PASSWORD = process.env.NEXT_PUBLIC_DEMO_PASSWORD ?? "";
const SHOW_DEMO_LOGIN =
  process.env.NEXT_PUBLIC_ENVIRONMENT === "development" && Boolean(DEMO_EMAIL && DEMO_PASSWORD);

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const tokens = await api<{ access_token: string }>("/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      // The refresh token arrives as an httpOnly cookie and is never read here.
      setTokens(tokens.access_token);
      router.push(params.get("next") || "/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="relative w-full max-w-md animate-rise rounded-3xl border border-white/10 bg-white p-8 shadow-2xl">
      <div className="mb-8">
        <div className="font-display text-3xl text-[var(--ink)]">GrowthOS</div>
        <p className="mt-2 text-sm text-[var(--muted)]">Sign in to your marketing operating system.</p>
      </div>
      <form className="space-y-4" onSubmit={onSubmit}>
        <div>
          <label className="mb-1.5 block text-xs font-medium uppercase tracking-wide text-[var(--muted)]">Email</label>
          <Input
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            type="email"
            autoComplete="username"
            required
          />
        </div>
        <div>
          <label className="mb-1.5 block text-xs font-medium uppercase tracking-wide text-[var(--muted)]">Password</label>
          <Input
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            type="password"
            autoComplete="current-password"
            required
          />
        </div>
        {error ? <p className="text-sm text-rose-600">{error}</p> : null}
        <Button className="w-full" disabled={loading} type="submit">
          {loading ? "Signing in..." : "Sign in"}
        </Button>
      </form>
      <p className="mt-5 text-center text-sm text-[var(--muted)]">
        New agency?{" "}
        <Link className="font-medium text-[var(--accent-ink)]" href="/register">
          Create account
        </Link>
      </p>
      {SHOW_DEMO_LOGIN ? (
        <div className="mt-4 rounded-xl border border-amber-300 bg-amber-50 p-3 text-center">
          <p className="text-xs font-medium text-amber-900">Development environment</p>
          <button
            className="mt-1 text-xs font-medium text-amber-900 underline"
            onClick={() => {
              setEmail(DEMO_EMAIL);
              setPassword(DEMO_PASSWORD);
            }}
            type="button"
          >
            Fill demo credentials
          </button>
        </div>
      ) : null}
    </div>
  );
}

export default function LoginPage() {
  return (
    <div className="relative flex min-h-screen items-center justify-center px-4">
      <div className="absolute inset-0 bg-[var(--panel)]" />
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_20%_20%,rgba(15,159,138,0.28),transparent_35%),radial-gradient(circle_at_80%_0%,rgba(255,255,255,0.08),transparent_30%)]" />
      <Suspense fallback={<div className="relative text-white">Loading...</div>}>
        <LoginForm />
      </Suspense>
    </div>
  );
}
