# Firebase Setup Guide for CMBAgent

This guide walks through setting up Firebase for authentication and Firestore for data persistence.

## Prerequisites

- Google Cloud account
- Node.js installed
- Python 3.12+ installed

## Step 1: Create Firebase Project

1. Go to [Firebase Console](https://console.firebase.google.com/)
2. Click "Create a project" (or "Add project")
3. Enter project name: `cmbagent` (or your preferred name)
4. Enable/disable Google Analytics (optional)
5. Click "Create project"

## Step 2: Enable Authentication

1. In Firebase Console, go to **Build** → **Authentication**
2. Click **Get started**
3. Go to **Sign-in method** tab
4. Enable **Google** provider:
   - Click on Google
   - Toggle "Enable"
   - Add your project support email
   - Click "Save"
5. (Optional) Enable other providers as needed (Email/Password, GitHub, etc.)

## Step 3: Enable Firestore

1. In Firebase Console, go to **Build** → **Firestore Database**
2. Click **Create database**
3. Choose **Start in production mode** (we'll set rules later)
4. Select your preferred location (closest to your users)
5. Click **Enable**

## Step 4: Set Firestore Security Rules

1. Go to **Firestore Database** → **Rules**
2. Replace the rules with:

```javascript
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    // Users can only access their own data
    match /users/{userId} {
      allow read, write: if request.auth != null && request.auth.uid == userId;
    }

    // Tasks belong to users
    match /tasks/{taskId} {
      allow read, write: if request.auth != null &&
        resource.data.user_id == request.auth.uid;
      allow create: if request.auth != null;
    }

    // Executions belong to users
    match /executions/{executionId} {
      allow read, write: if request.auth != null &&
        resource.data.user_id == request.auth.uid;
      allow create: if request.auth != null;
    }
  }
}
```

3. Click **Publish**

## Step 5: Get Firebase Configuration (Frontend)

1. In Firebase Console, go to **Project settings** (gear icon)
2. Scroll down to **Your apps**
3. Click **Add app** → **Web** (</> icon)
4. Register app with nickname: `cmbagent-ui`
5. Copy the Firebase configuration object

Create `cmbagent-ui/lib/firebase.ts`:

```typescript
import { initializeApp } from 'firebase/app';
import { getAuth, GoogleAuthProvider } from 'firebase/auth';

const firebaseConfig = {
  apiKey: "YOUR_API_KEY",
  authDomain: "YOUR_PROJECT_ID.firebaseapp.com",
  projectId: "YOUR_PROJECT_ID",
  storageBucket: "YOUR_PROJECT_ID.appspot.com",
  messagingSenderId: "YOUR_SENDER_ID",
  appId: "YOUR_APP_ID"
};

const app = initializeApp(firebaseConfig);
export const auth = getAuth(app);
export const googleProvider = new GoogleAuthProvider();
```

Or use environment variables:

```typescript
const firebaseConfig = {
  apiKey: process.env.NEXT_PUBLIC_FIREBASE_API_KEY,
  authDomain: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN,
  projectId: process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID,
  storageBucket: process.env.NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: process.env.NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID,
  appId: process.env.NEXT_PUBLIC_FIREBASE_APP_ID
};
```

## Step 6: Get Service Account Key (Backend)

1. In Firebase Console, go to **Project settings**
2. Go to **Service accounts** tab
3. Click **Generate new private key**
4. Save the JSON file as `backend/firebase-credentials.json`

**IMPORTANT**: Never commit this file to git! Add to `.gitignore`:

```
firebase-credentials.json
```

## Step 7: Install Dependencies

### Frontend (cmbagent-ui)

```bash
cd cmbagent-ui
npm install firebase
```

### Backend

```bash
cd backend
pip install firebase-admin google-cloud-firestore
```

Or add to `requirements.txt`:

```
firebase-admin>=6.0.0
google-cloud-firestore>=2.0.0
```

## Step 8: Environment Variables

### Frontend (.env.local)

Create `cmbagent-ui/.env.local`:

```bash
NEXT_PUBLIC_FIREBASE_API_KEY=your_api_key
NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN=your_project.firebaseapp.com
NEXT_PUBLIC_FIREBASE_PROJECT_ID=your_project_id
NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET=your_project.appspot.com
NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID=your_sender_id
NEXT_PUBLIC_FIREBASE_APP_ID=your_app_id
NEXT_PUBLIC_BACKEND_URL=http://localhost:8000
```

### Backend (.env)

Create `backend/.env`:

```bash
# Set to "false" to enable Firebase auth (production)
# Set to "true" for local development without Firebase
CMBAGENT_LOCAL_DEV=true

# Path to Firebase credentials (only needed when CMBAGENT_LOCAL_DEV=false)
FIREBASE_CREDENTIALS=firebase-credentials.json

# Google Cloud project ID
GOOGLE_CLOUD_PROJECT=your_project_id
```

## Step 9: Test the Setup

### Test Backend Auth (Local Dev Mode)

```bash
cd backend
CMBAGENT_LOCAL_DEV=true python -c "
from auth import get_current_user, is_local_dev
print(f'Local dev mode: {is_local_dev()}')
"
```

### Test Backend Auth (Production Mode)

```bash
cd backend
CMBAGENT_LOCAL_DEV=false python -c "
from auth import is_local_dev
print(f'Local dev mode: {is_local_dev()}')
# Should print False and initialize Firebase
"
```

### Test Frontend Firebase

```bash
cd cmbagent-ui
npm run dev
# Open http://localhost:3000 and check browser console for Firebase initialization
```

## Cloud Run Deployment

When deploying to Cloud Run, the backend uses Application Default Credentials automatically.

1. Enable required APIs in Google Cloud Console:
   - Cloud Run
   - Firestore
   - Identity and Access Management (IAM)

2. Grant Cloud Run service account Firestore access:
   ```bash
   gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
     --member="serviceAccount:YOUR_PROJECT_NUMBER-compute@developer.gserviceaccount.com" \
     --role="roles/datastore.user"
   ```

3. Deploy with environment variables:
   ```bash
   gcloud run deploy cmbagent-backend \
     --source . \
     --set-env-vars="CMBAGENT_LOCAL_DEV=false,GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID"
   ```

## Troubleshooting

### "Firebase app not initialized"

- Check that `firebase-credentials.json` exists in the backend directory
- Or ensure `GOOGLE_APPLICATION_CREDENTIALS` is set

### "Permission denied" in Firestore

- Check Firestore security rules
- Ensure the user is authenticated
- Verify the user ID matches the document's user_id field

### "Invalid token" errors

- Token may have expired (Firebase tokens expire after 1 hour)
- Refresh the token on the frontend
- Check clock synchronization between client and server

### Local development issues

- Set `CMBAGENT_LOCAL_DEV=true` to bypass Firebase auth
- The backend will use a mock user for testing

## Next Steps

1. Set up Firebase Hosting for the frontend (optional)
2. Configure custom domain
3. Set up Firebase App Check for additional security
4. Configure Firestore backup and export
