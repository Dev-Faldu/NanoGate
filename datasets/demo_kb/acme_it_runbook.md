# Acme Corp IT Service Desk Runbook

DEMO DATA — fictional company, written for NanoGate testing. Not real procedures.
Owner: IT Service Desk · Version 1.0 · Service desk: extension 4357 (HELP), helpdesk@acme.example, portal https://help.acme.example

## VPN: how to reset the VPN client (Acme Connect)

Acme uses the Acme Connect VPN client (based on GlobalProtect), gateway vpn.acme.example.
To reset the VPN client on your own laptop:
1. Click the Acme Connect icon in the system tray (Windows) or menu bar (Mac) and choose Disconnect.
2. Open the menu (three dots) and choose Settings > Troubleshooting > Reset Client. This clears the cached gateway and certificates.
3. Quit Acme Connect completely, then start it again.
4. Enter the portal address vpn.acme.example and sign in with your Acme email and password, then approve the MFA prompt.
5. If it still fails, restart the laptop and try once more on a different network (for example a phone hotspot).
If the client is still stuck after a reset, open a ticket on the portal with category "VPN" and attach the log from Settings > Troubleshooting > Collect Logs.
Only IT can reset VPN access for another person or for all users; employees can only reset their own client.
Uninstalling the VPN client is not allowed on managed laptops.

## Passwords: how to change your Windows password

Acme Windows passwords must be at least 14 characters, expire every 180 days, and cannot reuse your last 10 passwords.
To change your own Windows password while in the office or on VPN:
1. Press Ctrl+Alt+Delete and choose "Change a password".
2. Enter your current password, then the new password twice.
3. Lock and unlock the laptop so the new password is cached.
If you are working remotely, connect to Acme Connect VPN first, otherwise the laptop keeps the old password.
If you forgot your password, use self-service reset at https://password.acme.example (requires MFA).
You can never change another person's password; managers must ask the Service Desk. Password expiry cannot be switched off.

## Printers: how to add the office printer on a Mac

Office printers are named ACME-<floor>-<number>, for example ACME-3-01 on floor 3. They use the print queue print.acme.example with Follow-Me printing.
To add the office printer on your Mac:
1. Connect to office Wi-Fi (ACME-CORP) or the VPN.
2. Open the Self Service app and install "Acme Follow-Me Printer". This adds the printer and the driver automatically.
3. Print as normal and choose "Acme Follow-Me". Tap your badge on any office printer to release the job.
Manual fallback: System Settings > Printers & Scanners > Add Printer > IP, address print.acme.example, protocol IPP.
Printers are removed through Self Service > Uninstall. Only IT deploys printers to all Macs in a building.

## MFA: how to enroll a new phone for multi-factor authentication

Acme uses Microsoft Authenticator push notifications for MFA.
To enroll a new or replacement phone:
1. Install Microsoft Authenticator on the new phone.
2. On a laptop, go to https://mysignins.acme.example > Security info and sign in (approve with the old phone if you still have it).
3. Choose Add sign-in method > Authenticator app and scan the QR code with the new phone.
4. Approve the test notification, then delete the old phone from the list.
Lost your old phone? Call the Service Desk on extension 4357; they verify your identity by video call and issue a temporary access pass valid for 8 hours.
MFA cannot be disabled on any Acme account, and you can only enroll devices on your own account.

## Wi-Fi: guest network name and how visitors join

The guest Wi-Fi network is called ACME-GUEST. It is internet-only and cannot reach internal systems.
How visitors join:
1. The visitor connects to ACME-GUEST.
2. A sign-in page opens; they enter their name, email and the name of their Acme host.
3. The host receives an email and clicks Approve. Access lasts 24 hours.
Employees use ACME-CORP, which signs in automatically with the laptop certificate; there is no shared password for the corporate Wi-Fi.

## Encryption: how to check your laptop disk is encrypted

All Acme laptops must have full-disk encryption (BitLocker on Windows, FileVault on Mac).
Windows: open Settings > Privacy & security > Device encryption, or search "Manage BitLocker"; drive C: must show "BitLocker on".
Mac: open System Settings > Privacy & Security > FileVault; it must say "FileVault is turned on".
If encryption is off, do not turn it on yourself; open a ticket so IT can escrow the recovery key. Turning encryption off is not permitted.
IT reports on encryption for all laptops centrally; employees only check their own.

## Email: how to update your Outlook email signature

Acme signature format: Name, Job title, Acme Corp, phone number. No images or quotes.
Outlook for Windows: File > Options > Mail > Signatures > New, then set it as default for new messages and replies.
Outlook on the web: Settings (gear) > Mail > Compose and reply > Email signature.
New Outlook for Mac: Outlook > Settings > Signatures.
You can only edit your own signature. Executive assistants with delegate access should contact IT for executive signatures.

## Software: how to request an Adobe Acrobat license

Adobe Acrobat Reader is free and available in Self Service / Company Portal. Acrobat Pro needs a paid license.
To request an Acrobat Pro license:
1. Open https://help.acme.example > Request something > Software license > Adobe Acrobat Pro.
2. Enter a business reason; your manager approves automatically by email.
3. Once approved (usually within 1 business day), sign in to Acrobat with your Acme email.
Licenses for a whole team need a request from the department head and a cost centre.

## Shared drives: how to access your team's shared drive from home

Team shared drives live in SharePoint / OneDrive, so they work from home without VPN.
1. Open https://acme.sharepoint.example and choose your team site, then Documents.
2. Click "Sync" to add it to File Explorer (Windows) or Finder (Mac).
Old network drives (\\files.acme.example\teams) need the Acme Connect VPN.
You only have access to your own team's drive. Access to another team's drive must be approved by that team's owner via a portal request.

## Backups: how to restore a deleted file from last week's backup

OneDrive and SharePoint keep deleted files for 93 days.
1. Open OneDrive or the SharePoint site on the web and choose Recycle bin.
2. Select the file and click Restore.
3. To roll a whole folder back to last week, use Settings > Restore your OneDrive and pick a date.
Files on laptops outside OneDrive are not backed up. Server backups are kept for 30 days and restored only by IT via a ticket.

## Teams: how to record a Teams meeting

1. In the meeting, click More (…) > Record and transcribe > Start recording.
2. All attendees are notified. Tell external guests before you start.
3. Recordings are saved to OneDrive (non-channel meetings) or SharePoint (channel meetings) and expire after 120 days.
Recording is disabled for meetings labelled Confidential. Do not record HR or performance conversations.

## Phishing: how to report a phishing email

1. In Outlook, click the "Report" button on the ribbon and choose "Phishing".
2. Do not click links, open attachments or forward the email to colleagues.
3. If you already clicked a link or entered your password, call the Service Desk immediately on extension 4357 and change your password at https://password.acme.example.
The security team reviews reports within 4 business hours.
