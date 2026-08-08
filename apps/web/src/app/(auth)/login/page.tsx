"use client";

import Link from "next/link";
import { FormEvent, Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { api, setTokens } from "@/lib/api";

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [email, setEmail] = useState("demo@growthos.ai");
  const [password, setPassword] = useState("demo1234");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const tokens = await api<{ access_token: string; refresh_token: string }>("/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      setTokens(tokens.access_token, tokens.refresh_token);
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
          <Input value={email} onChange={(e) => setEmail(e.target.value)} type="email" required />
        </div>
        <div>
          <label className="mb-1.5 block text-xs font-medium uppercase tracking-wide text-[var(--muted)]">Password</label>
          <Input value={password} onChange={(e) => setPassword(e.target.value)} type="password" required />
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
      <p className="mt-3 text-center text-xs text-[var(--muted)]">Demo: demo@growthos.ai / demo1234</p>
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
