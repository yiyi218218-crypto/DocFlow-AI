import Link from 'next/link';

export default function NotFound() {
  return (
    <main
      id="main"
      className="mx-auto flex min-h-dvh max-w-md flex-col items-center justify-center px-6 text-center"
    >
      <p className="font-mono text-xs text-ink-3">404</p>
      <h1 className="mt-3 text-xl font-semibold">This page doesn&rsquo;t exist</h1>
      <p className="mt-2 text-sm text-ink-2">
        The link may be out of date, or the document may have been deleted.
      </p>
      <Link
        href="/documents"
        className="mt-6 inline-flex h-9 items-center rounded-[--radius-inner] bg-accent px-4 text-[13px] font-medium text-white transition-all duration-200 hover:bg-accent-hover active:scale-[0.98]"
      >
        Back to your documents
      </Link>
    </main>
  );
}
