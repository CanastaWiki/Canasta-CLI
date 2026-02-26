# Email

Canasta includes a built-in mail server ([Postfix](https://www.postfix.org/)) that lets your wiki send email out of the box. This covers account confirmations, password resets, watchlist notifications, and user-to-user email.

## Contents

- [How it works](#how-it-works)
- [Improving deliverability](#improving-deliverability)
- [Disabling the built-in mail server](#disabling-the-built-in-mail-server)
- [Disabling email entirely](#disabling-email-entirely)
- [Troubleshooting](#troubleshooting)

---

## How it works

Postfix runs inside the Canasta container as a local-only mail transfer agent. When MediaWiki sends an email (via PHP's `mail()` function), Postfix accepts the message and delivers it directly to the recipient's mail server by looking up MX records in DNS.

No configuration is needed — email works immediately after `canasta create`.

The sender domain is derived from your wiki's URL (`MW_SITE_SERVER`). For example, if your wiki is at `https://wiki.example.com`, outgoing mail will come from `wiki.example.com`.

!!! warning
    Messages sent from the built-in mail server may be flagged as spam by recipients, because the server has no SPF, DKIM, or DMARC records. This is fine for development and testing, but for production wikis you should [configure an external SMTP provider](#improving-deliverability).

---

## Improving deliverability

For production use, configure [`$wgSMTP`](https://www.mediawiki.org/wiki/Manual:$wgSMTP) in your wiki's settings file to route email through an authenticated SMTP provider. This gives you proper SPF/DKIM alignment and much better inbox placement.

Example using a generic SMTP provider:

```php
$wgSMTP = [
    'host'     => 'ssl://smtp.example.com',
    'IDHost'   => 'example.com',
    'port'     => 465,
    'auth'     => true,
    'username' => 'your-username',
    'password' => 'your-password',
];
```

Popular providers include [Amazon SES](https://aws.amazon.com/ses/), [SendGrid](https://sendgrid.com/), [Mailgun](https://www.mailgun.com/), and [Brevo](https://www.brevo.com/). Most offer a free tier sufficient for small wikis.

Once `$wgSMTP` is configured, MediaWiki connects directly to the external provider — Postfix is bypassed entirely. You can then [disable the built-in mail server](#disabling-the-built-in-mail-server) to avoid running an unused service.

---

## Disabling the built-in mail server

If you configure an external SMTP provider via `$wgSMTP`, you can disable Postfix:

```bash
canasta config set MW_ENABLE_POSTFIX=false -i myinstance
canasta restart -i myinstance
```

To re-enable it:

```bash
canasta config set MW_ENABLE_POSTFIX=true -i myinstance
canasta restart -i myinstance
```

---

## Disabling email entirely

To prevent the wiki from sending any email at all, set [`$wgEnableEmail`](https://www.mediawiki.org/wiki/Manual:$wgEnableEmail) to `false` in your wiki's settings file:

```php
$wgEnableEmail = false;
```

---

## Troubleshooting

### Emails not being sent

- Verify Postfix is running: `docker exec <container> service postfix status`
- Check Postfix is enabled: `canasta config get MW_ENABLE_POSTFIX -i myinstance`
- Check the mail log inside the container: `docker exec <container> cat /var/log/postfix.log`

### Emails landing in spam

This is expected with the built-in mail server. To fix it, [configure an external SMTP provider](#improving-deliverability) and set up SPF, DKIM, and DMARC records for your domain.

### Emails not sent after configuring $wgSMTP

- Verify the SMTP credentials are correct by testing with a simple PHP script
- Check that your hosting provider does not block outbound connections on the SMTP port (465 or 587)
- Look for errors in the MediaWiki debug log (set `$wgDebugLogFile` temporarily)
