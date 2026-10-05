# Microsoft 365 (Outlook Mail + Calendar)

Grain signs in to Microsoft with a public-client Entra app registration: PKCE, a loopback redirect, no client
secret. Only the application (client) id is configuration. Access and refresh tokens are kept in the Keychain.

## Register the app

1. Entra admin center (or Azure portal) > **App registrations** > **New registration**.
2. **Supported account types**: *Accounts in any organizational directory and personal Microsoft accounts*
   (this is what the default `common` authority needs). For work accounts only, pick the multitenant option and
   set `MICROSOFT_TENANT=organizations`; for personal only, `consumers`.
3. Leave the redirect URI blank for now and register.
4. **Authentication** > **Add a platform** > **Mobile and desktop applications**. Tick or add any redirect URI
   (for example `http://localhost`); it only creates the platform.
5. **Authentication** > **Advanced settings** > **Allow public client flows** = **Yes**. Save.
6. **API permissions** > **Add a permission** > **Microsoft Graph** > **Delegated**: `User.Read`, `Mail.ReadWrite`,
   `Mail.Send`, `Calendars.ReadWrite`, `offline_access`, `openid`, `profile`.
7. Copy the **Application (client) ID** into `MICROSOFT_CLIENT_ID` in `.env` (see `.env.example`), or paste it
   into Settings > Integrations. Restart the app after editing `.env`.

## The redirect URI

Grain's backend listens on `127.0.0.1` and sends `http://127.0.0.1:<port>/integrations/microsoft/callback` as
the redirect. Entra ignores the port for loopback redirects, but the host and path must match what is registered.
Microsoft recommends the `127.0.0.1` literal over `localhost`, and accepts it, but the portal's redirect URI box
refuses an `http://127.0.0.1` entry. Add it in the manifest instead:

1. App registration > **Manifest**.
2. Under `publicClient` (older manifests: `replyUrlsWithType` with type `InstalledClient`), add
   `"http://127.0.0.1/integrations/microsoft/callback"` to `redirectUris`. Save.

Do not register several loopback URIs that differ only by port; Entra picks one arbitrarily.

If sign-in ends on a page reading `AADSTS50011`, the registered redirect does not match; re-check the host
(`127.0.0.1`, not `localhost`) and the path.

## Admin consent

A work or school tenant may require an administrator to approve the permissions (`AADSTS65001`). Ask the
tenant admin to grant consent for the app, or sign in with a personal Microsoft account.

## Selecting the provider

Mail and Calendar follow the `pimProvider` setting (`google` by default, or `microsoft`). One provider is active
at a time. Signing out clears the stored tokens; a public client has no revoke endpoint, so remove the app's
access at https://account.microsoft.com/privacy/app-access (personal) or My Apps (work) if you want it gone.
