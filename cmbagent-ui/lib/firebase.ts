/**
 * Firebase Configuration for CMBAgent UI
 *
 * Handles Firebase initialization, authentication, and auth state management.
 */

import { initializeApp, getApps, FirebaseApp } from 'firebase/app';
import {
  getAuth,
  GoogleAuthProvider,
  signInWithPopup,
  signOut as firebaseSignOut,
  onAuthStateChanged,
  User,
  Auth,
} from 'firebase/auth';

// Firebase configuration from environment variables
const firebaseConfig = {
  apiKey: process.env.NEXT_PUBLIC_FIREBASE_API_KEY,
  authDomain: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN,
  projectId: process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID,
  storageBucket: process.env.NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: process.env.NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID,
  appId: process.env.NEXT_PUBLIC_FIREBASE_APP_ID,
};

// Check if Firebase is configured
const isFirebaseConfigured = (): boolean => {
  return !!(
    firebaseConfig.apiKey &&
    firebaseConfig.authDomain &&
    firebaseConfig.projectId
  );
};

// Initialize Firebase (singleton pattern)
let app: FirebaseApp | null = null;
let auth: Auth | null = null;

const initializeFirebase = (): { app: FirebaseApp | null; auth: Auth | null } => {
  if (typeof window === 'undefined') {
    // Server-side, return null
    return { app: null, auth: null };
  }

  if (!isFirebaseConfigured()) {
    console.warn('Firebase not configured. Running in local development mode.');
    return { app: null, auth: null };
  }

  if (!app) {
    const existingApps = getApps();
    if (existingApps.length > 0) {
      app = existingApps[0];
    } else {
      app = initializeApp(firebaseConfig);
    }
    auth = getAuth(app);
  }

  return { app, auth };
};

// Initialize on module load (client-side only)
if (typeof window !== 'undefined') {
  initializeFirebase();
}

// Google Auth Provider
const googleProvider = new GoogleAuthProvider();
googleProvider.setCustomParameters({
  prompt: 'select_account',
});

/**
 * Sign in with Google popup.
 */
export const signInWithGoogle = async (): Promise<User | null> => {
  const { auth } = initializeFirebase();

  if (!auth) {
    console.warn('Firebase not initialized, skipping sign in');
    return null;
  }

  try {
    const result = await signInWithPopup(auth, googleProvider);
    return result.user;
  } catch (error: any) {
    console.error('Sign in error:', error);
    throw error;
  }
};

/**
 * Sign out the current user.
 */
export const signOut = async (): Promise<void> => {
  const { auth } = initializeFirebase();

  if (!auth) {
    return;
  }

  await firebaseSignOut(auth);
};

/**
 * Get the current user.
 */
export const getCurrentUser = (): User | null => {
  const { auth } = initializeFirebase();
  return auth?.currentUser || null;
};

/**
 * Get the current user's ID token.
 */
export const getIdToken = async (): Promise<string | null> => {
  const { auth } = initializeFirebase();
  const user = auth?.currentUser;

  if (!user) {
    return null;
  }

  try {
    return await user.getIdToken();
  } catch (error) {
    console.error('Error getting ID token:', error);
    return null;
  }
};

/**
 * Subscribe to auth state changes.
 */
export const onAuthChange = (callback: (user: User | null) => void): (() => void) => {
  const { auth } = initializeFirebase();

  if (!auth) {
    // No Firebase, call with null immediately
    callback(null);
    return () => {};
  }

  return onAuthStateChanged(auth, callback);
};

/**
 * Check if Firebase is available.
 */
export const isFirebaseAvailable = (): boolean => {
  return isFirebaseConfigured() && typeof window !== 'undefined';
};

/**
 * Check if user is signed in.
 */
export const isSignedIn = (): boolean => {
  return !!getCurrentUser();
};

// Export auth instance for direct access if needed
export { auth, app };
