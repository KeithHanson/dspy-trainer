## Overview

This guide walks through wiring Auth0 into a React single-page application (SPA) using the official `@auth0/auth0-react` SDK. You will:

- Configure an Auth0 Single Page Application
- Install and configure the React SDK
- Add login/logout buttons and a profile view
- Protect routes with a simple `ProtectedRoute` component

***

## 1. Configure Auth0 for a React SPA

1. Create an Auth0 tenant (if you do not already have one) and go to the **Applications → Applications** section.  
2. Create a new **Single Page Application**. [github](https://github.com/auth0/auth0-react)
3. In the application **Settings**, configure URLs (for a dev app running on `http://localhost:3000`): [auth0.github](https://auth0.github.io/auth0-react/)

   - Allowed Callback URLs: `http://localhost:3000`  
   - Allowed Logout URLs: `http://localhost:3000`  
   - Allowed Web Origins: `http://localhost:3000`  

4. Under **Basic Information**, note the **Domain** and **Client ID** values; these will go into your React app configuration. [github](https://github.com/auth0/auth0-react)

For production, add your real domain(s) to these URL lists as well.

***

## 2. Install the Auth0 React SDK

From your SPA project root:

```bash
npm install @auth0/auth0-react
# or
yarn add @auth0/auth0-react
```

The `@auth0/auth0-react` package provides the `Auth0Provider` context and the `useAuth0` hook for accessing authentication state and methods. [auth0.github](https://auth0.github.io/auth0-react/)

***

## 3. Wrap the App with Auth0Provider

Create a configuration file for your Auth0 settings, for example `src/auth0-config.ts`:

```ts
// src/auth0-config.ts
export const auth0Config = {
  domain: import.meta.env.VITE_AUTH0_DOMAIN,
  clientId: import.meta.env.VITE_AUTH0_CLIENT_ID,
  authorizationParams: {
    redirect_uri: window.location.origin,
    audience: import.meta.env.VITE_AUTH0_AUDIENCE, // optional, for APIs
    scope: "openid profile email",                 // adjust as needed
  },
};
```

Then wrap your root component with `Auth0Provider` in your entry file (`src/main.tsx` or `src/index.tsx`): [codefinity](https://codefinity.com/courses/v2/b38ea379-a33a-4c20-9bc7-a638023cac7f/e2fea5b7-54a6-4428-ad20-4528f7101b37/c31d2528-f981-43c5-960e-48bf6beb2c9b)

```tsx
// src/main.tsx
import React from "react";
import ReactDOM from "react-dom/client";
import { Auth0Provider } from "@auth0/auth0-react";
import App from "./App";
import { auth0Config } from "./auth0-config";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <Auth0Provider
      domain={auth0Config.domain}
      clientId={auth0Config.clientId}
      authorizationParams={auth0Config.authorizationParams}
    >
      <App />
    </Auth0Provider>
  </React.StrictMode>
);
```

This sets up a React Context so any descendant component can call `useAuth0()` to access auth state and methods. [auth0](https://auth0.com/docs/libraries/auth0-react)

***

## 4. Add Login, Logout, and Profile Components

### Login button

```tsx
// src/components/LoginButton.tsx
import { useAuth0 } from "@auth0/auth0-react";

export function LoginButton() {
  const { loginWithRedirect, isAuthenticated } = useAuth0();

  if (isAuthenticated) return null;

  return (
    <button onClick={() => loginWithRedirect()}>
      Log In
    </button>
  );
}
```

`loginWithRedirect` triggers the Auth0 Universal Login page and then redirects back to your SPA. [github](https://github.com/auth0/auth0-react)

### Logout button

```tsx
// src/components/LogoutButton.tsx
import { useAuth0 } from "@auth0/auth0-react";

export function LogoutButton() {
  const { logout, isAuthenticated } = useAuth0();

  if (!isAuthenticated) return null;

  return (
    <button
      onClick={() =>
        logout({
          logoutParams: {
            returnTo: window.location.origin,
          },
        })
      }
    >
      Log Out
    </button>
  );
}
```

Ensure the `returnTo` URL is included in **Allowed Logout URLs** in your Auth0 app settings. [auth0](https://auth0.com/docs/libraries/auth0-react)

### Profile component

```tsx
// src/components/Profile.tsx
import { useAuth0 } from "@auth0/auth0-react";

export function Profile() {
  const { user, isAuthenticated, isLoading } = useAuth0();

  if (isLoading) return <div>Loading profile...</div>;
  if (!isAuthenticated) return null;

  return (
    <div>
      <img src={user?.picture} alt={user?.name} />
      <h2>{user?.name}</h2>
      <p>{user?.email}</p>
    </div>
  );
}
```

The `useAuth0` hook exposes `user`, `isAuthenticated`, and `isLoading` to drive your UI based on authentication state. [codefinity](https://codefinity.com/courses/v2/b38ea379-a33a-4c20-9bc7-a638023cac7f/e2fea5b7-54a6-4428-ad20-4528f7101b37/c31d2528-f981-43c5-960e-48bf6beb2c9b)

***

## 5. Use Auth0 in the Main App

In your main component, you can render login/logout buttons and a profile:

```tsx
// src/App.tsx
import { useAuth0 } from "@auth0/auth0-react";
import { LoginButton } from "./components/LoginButton";
import { LogoutButton } from "./components/LogoutButton";
import { Profile } from "./components/Profile";

function App() {
  const { isAuthenticated } = useAuth0();

  return (
    <div>
      <header>
        <h1>My React SPA</h1>
        <LoginButton />
        <LogoutButton />
      </header>

      <main>
        {isAuthenticated ? (
          <>
            <h2>Welcome!</h2>
            <Profile />
          </>
        ) : (
          <p>Please log in to continue.</p>
        )}
      </main>
    </div>
  );
}

export default App;
```

This gives a minimal authenticated experience: users log in with Auth0, see their profile, and can log out. [auth0](https://auth0.com/docs/quickstart/spa/react)

***

## 6. Protecting Routes (with React Router)

If using React Router, define a `ProtectedRoute` component that checks `isAuthenticated` and optionally calls `loginWithRedirect`:

```tsx
// src/components/ProtectedRoute.tsx
import { useEffect } from "react";
import { useAuth0 } from "@auth0/auth0-react";
import { useLocation } from "react-router-dom";

interface ProtectedRouteProps {
  children: JSX.Element;
}

export function ProtectedRoute({ children }: ProtectedRouteProps) {
  const { isAuthenticated, isLoading, loginWithRedirect } = useAuth0();
  const location = useLocation();

  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      void loginWithRedirect({
        appState: { returnTo: location.pathname },
      });
    }
  }, [isAuthenticated, isLoading, loginWithRedirect, location.pathname]);

  if (isLoading || !isAuthenticated) {
    return <div>Loading...</div>;
  }

  return children;
}
```

Then wrap protected routes:

```tsx
// src/router.tsx (example)
import { BrowserRouter, Routes, Route } from "react-router-dom";
import App from "./App";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { Dashboard } from "./pages/Dashboard";

export function Router() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<App />} />
        <Route
          path="/dashboard"
          element={
            <ProtectedRoute>
              <Dashboard />
            </ProtectedRoute>
          }
        />
      </Routes>
    </BrowserRouter>
  );
}
```

The SDK will restore `appState.returnTo` after login so users land on the protected route they originally requested. [auth0](https://auth0.com/docs/quickstart/spa/react)

***

## 7. Environment Variables and Build Configuration

- Put your secrets in environment variables, such as `VITE_AUTH0_DOMAIN` and `VITE_AUTH0_CLIENT_ID` for Vite, or `REACT_APP_AUTH0_DOMAIN`/`REACT_APP_AUTH0_CLIENT_ID` for Create React App. [dev](https://dev.to/kleeut/auth0-and-react-getting-started-2oig)
- Ensure your build system exposes these variables at runtime and that you update Auth0’s allowed URLs to match dev, staging, and production origins. [auth0](https://auth0.com/docs/quickstart/spa/react)

***

## 8. Minimal File Overview

For a simple SPA, you might end up with:

- `src/auth0-config.ts` – Auth0 configuration (domain, clientId, audience, scopes)
- `src/main.tsx` – Root render with `Auth0Provider` wrapper
- `src/App.tsx` – Main UI with login/logout and profile
- `src/components/LoginButton.tsx`
- `src/components/LogoutButton.tsx`
- `src/components/Profile.tsx`
- `src/components/ProtectedRoute.tsx` (if routing is used)

This structure keeps Auth0 concerns modular and easy to expand (e.g., adding roles, calling protected APIs with access tokens). [auth0](https://auth0.com/docs/libraries/auth0-react)
