"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { api, setTokens } from "@/lib/api";

export default function RegisterPage() {
  const router = useRouter();
  const [form, setForm] = useState({
    full_name: "",
    email: "",
    password: "",
    organization_name: "",
  });
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const tokens = await api<{ access_token: string; refresh_token: string }>("/auth/register", {
        method: "POST",
        body: JSON.stringify(form),
      });
      setTokens(tokens.access_token, tokens.refresh_token);
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Registration failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center px-4">
      <div className="absolute inset-0 bg-[var(--panel)]" />
      <div className="relative w-full max-w-md animate-rise rounded-3xl border border-white/10 bg-white p-8 shadow-2xl">
        <div className="mb-8">
          <div className="font-display text-3xl text-[var(--ink)]">Create GrowthOS</div>
          <p className="mt-2 text-sm text-[var(--muted)]">Spin up a multi-tenant agency workspace.</p>
        </div>
        <form className="space-y-4" onSubmit={onSubmit}>
          {[
            ["full_name", "Full name"],
            ["organization_name", "Organization"],
            ["email", "Email"],
            ["password", "Password"],
          ].map(([key, label]) => (
            <div key={key}>
              <label className="mb-1.5 block text-xs font-medium uppercase tracking-wide text-[var(--muted)]">{label}</label>
              <Input
                type={key === "password" ? "password" : key === "email" ? "email" : "text"}
                required
                value={form[key as keyof typeof form]}
                onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
              />
            </div>
          ))}
          {error ? <p className="text-sm text-rose-600">{error}</p> : null}
          <Button className="w-full" disabled={loading} type="submit">
            {loading ? "Creating..." : "Create account"}
          </Button>
        </form>
        <p className="mt-5 text-center text-sm text-[var(--muted)]">
          Already have an account?{" "}
          <Link className="font-medium text-[var(--accent-ink)]" href="/login">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}
