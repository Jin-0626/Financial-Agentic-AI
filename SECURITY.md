# Security

Never include credentials, private uploaded files or sensitive research data in public
issues or pull requests. Use GitHub private vulnerability reporting if the repository
owner enables it; otherwise contact the owner privately before sharing sensitive details.
No private reporting channel has been configured by this local preparation.

Keep .env and credential-bearing sandbox.toml private. If a credential is exposed,
rotate it with its provider and remove it from source; deletion alone does not revoke it.
The Docker-socket management server is intended for a trusted local environment.

After creating the GitHub repository, enable secret scanning and push protection where
available. Availability depends on repository visibility and the GitHub plan. Do not
bypass protection for real credentials. These server-side settings are not enabled by
adding this document and have not been configured by this preparation.
