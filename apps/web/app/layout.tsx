import type { Metadata, Viewport } from 'next';
import { GeistMono } from 'geist/font/mono';
import { GeistSans } from 'geist/font/sans';
import { Providers } from './providers';
import './globals.css';

export const metadata: Metadata = {
  metadataBase: new URL('https://docflow-ai-six.vercel.app'),
  title: {
    default: 'DocFlow — deadlines you can trace',
    template: '%s · DocFlow',
  },
  description:
    'Upload a circular and get its deadlines, requirements and tasks — each one traceable to the exact line it came from.',
  openGraph: {
    title: 'DocFlow — deadlines you can trace',
    description:
      'Every extracted fact carries a verified quote. If it cannot be matched to the source, it is not shown as confirmed.',
    type: 'website',
  },
  // An emoji favicon as a data URI: no extra asset, no 404 in the network tab.
  icons: {
    icon: 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y=".9em" font-size="90">◲</text></svg>',
  },
};

export const viewport: Viewport = {
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: '#fdfcfa' },
    { media: '(prefers-color-scheme: dark)', color: '#1b1a18' },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="en"
      className={`${GeistSans.variable} ${GeistMono.variable}`}
      suppressHydrationWarning
    >
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        {/* Bengali is a first-class language here, not a fallback. */}
        <link
          href="https://fonts.googleapis.com/css2?family=Noto+Sans+Bengali:wght@400;500;600&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>
        <a href="#main" className="skip-link">
          Skip to content
        </a>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
