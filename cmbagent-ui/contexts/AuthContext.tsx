'use client'

import React, { createContext, useContext, useEffect, useState, useCallback } from 'react'
import { User } from 'firebase/auth'
import {
  onAuthChange,
  signInWithGoogle,
  signOut as firebaseSignOut,
  isFirebaseAvailable,
  getIdToken,
} from '@/lib/firebase'

interface AuthContextType {
  user: User | null
  loading: boolean
  isAuthenticated: boolean
  isLocalMode: boolean
  signIn: () => Promise<void>
  signOut: () => Promise<void>
  getToken: () => Promise<string | null>
}

const AuthContext = createContext<AuthContextType | undefined>(undefined)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const isLocalMode = !isFirebaseAvailable()

  useEffect(() => {
    if (isLocalMode) {
      // No Firebase - local dev mode
      setLoading(false)
      return
    }

    // Subscribe to auth state changes
    const unsubscribe = onAuthChange((firebaseUser) => {
      setUser(firebaseUser)
      setLoading(false)
    })

    return () => unsubscribe()
  }, [isLocalMode])

  const signIn = useCallback(async () => {
    if (isLocalMode) {
      console.log('Local dev mode - sign in not required')
      return
    }

    try {
      await signInWithGoogle()
    } catch (error) {
      console.error('Sign in error:', error)
      throw error
    }
  }, [isLocalMode])

  const signOut = useCallback(async () => {
    if (isLocalMode) {
      return
    }

    try {
      await firebaseSignOut()
    } catch (error) {
      console.error('Sign out error:', error)
      throw error
    }
  }, [isLocalMode])

  const getToken = useCallback(async (): Promise<string | null> => {
    if (isLocalMode) {
      return null
    }
    return getIdToken()
  }, [isLocalMode])

  const value: AuthContextType = {
    user,
    loading,
    isAuthenticated: !!user || isLocalMode,
    isLocalMode,
    signIn,
    signOut,
    getToken,
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextType {
  const context = useContext(AuthContext)
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return context
}
