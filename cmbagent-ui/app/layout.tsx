import type { Metadata } from 'next'
import { Inter } from 'next/font/google'
import './globals.css'
import { AuthProvider } from '@/contexts/AuthContext'

const inter = Inter({ subsets: ['latin'] })

export const metadata: Metadata = {
  title: 'CMBAGENT',
  description: 'Interactive UI for CMBAgent - Autonomous Research Backend ',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en">
      <head>
        <link href="https://fonts.googleapis.com/css2?family=Jersey+10&display=swap" rel="stylesheet" />
      </head>
      <body className={inter.className}>
        <AuthProvider>
          <div className="min-h-screen bg-gradient-to-br from-slate-900 via-blue-900 to-slate-900">
            {children}
          </div>
        </AuthProvider>
      </body>
    </html>
  )
}
