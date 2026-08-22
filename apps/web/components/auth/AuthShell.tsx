'use client';

/**
 * Shared frame for every auth screen.
 *
 * Sign-in, sign-up, forgot-password and reset-password differ only in their
 * form. Duplicating the layout four times is how the panels drift apart, so the
 * chrome lives here and each page supplies its heading, body and footer link.
 */

import Link from 'next/link';
import { useId } from 'react';

export function AuthShell({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string;
  subtitle: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
}) {
  return (
    <main id="main" className="grid min-h-dvh lg:grid-cols-2">
      <div className="flex items-center justify-center px-5 py-12 sm:px-6 sm:py-16">
        <div className="w-full max-w-sm">
          <Link
            href="/"
            className="mb-8 inline-flex items-center gap-2 text-[14px] font-semibold tracking-[-0.02em]"
          >
            <span aria-hidden className="text-accent">
              ◲
            </span>
            DocFlow
          </Link>

          <h1 className="text-[21px] font-semibold tracking-[-0.02em]">{title}</h1>
          <p className="mt-1.5 text-[13.5px] text-ink-2">{subtitle}</p>

          {children}

          {footer && <div className="mt-7 text-[13px] text-ink-2">{footer}</div>}
        </div>
      </div>

      {/* States the thesis rather than decorating. */}
      <div className="hidden border-l border-line bg-raised lg:flex lg:items-center lg:justify-center lg:p-16">
        <div className="max-w-sm">
          <p className="eyebrow text-accent">The provenance chain</p>
          <p className="mt-4 max-w-[46ch] text-[15px] leading-relaxed">
            Every extracted fact carries a verified quote. If the quote cannot be matched against
            the source text, the fact is not shown as confirmed.
          </p>
          <ol className="mt-7 flex flex-col gap-2 font-mono text-[11.5px] text-ink-2">
            {['document', 'page + word boxes', 'character span', 'rectangle', 'fact', 'task'].map(
              (step, index) => (
                <li key={step} className="flex items-center gap-2">
                  <span className="w-5 text-ink-4">{String(index + 1).padStart(2, '0')}</span>
                  <span className={index === 2 || index === 3 ? 'text-accent' : undefined}>
                    {step}
                  </span>
                </li>
              ),
            )}
          </ol>
        </div>
      </div>
    </main>
  );
}

export function Field({
  label,
  hint,
  action,
  id,
  ...props
}: React.InputHTMLAttributes<HTMLInputElement> & {
  label: string;
  hint?: string;
  /** Sits opposite the label — "Forgot password?" and the like. */
  action?: React.ReactNode;
}) {
  // Wrapping the input in the <label> would fold the hint into its accessible
  // name — a screen reader announcing "New password At least 10 characters" as
  // the field's name. The hint is a description; it needs its own association.
  const generatedId = useId();
  const inputId = id ?? generatedId;
  const hintId = `${inputId}-hint`;

  return (
    <div className="flex flex-col gap-1.5">
      <span className="flex items-baseline justify-between">
        <label htmlFor={inputId} className="text-[12px] font-medium text-ink-2">
          {label}
        </label>
        {action}
      </span>
      <input
        {...props}
        id={inputId}
        aria-describedby={hint ? hintId : undefined}
        // 16px on phones: anything smaller makes iOS zoom the viewport on focus.
        className="h-11 rounded-[--radius-inner] border border-line bg-surface px-3 text-[16px] outline-none transition-colors duration-200 focus:border-accent sm:h-9 sm:text-[13.5px]"
      />
      {hint && (
        <span id={hintId} className="text-[11.5px] text-ink-3">
          {hint}
        </span>
      )}
    </div>
  );
}

export function SubmitButton({
  busy,
  children,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { busy?: boolean }) {
  return (
    <button
      {...props}
      type="submit"
      disabled={busy || props.disabled}
      className="mt-1 h-11 rounded-[--radius-inner] bg-accent text-[14px] font-medium text-white shadow-[var(--shadow-raise)] transition-all duration-200 hover:bg-accent-hover active:scale-[0.98] disabled:opacity-50 sm:h-10"
    >
      {children}
    </button>
  );
}

export function FormError({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    // Next's route announcer is also role="alert", so tests need a stable hook.
    <p role="alert" data-testid="form-error" className="text-[12.5px] text-crit">
      {message}
    </p>
  );
}
