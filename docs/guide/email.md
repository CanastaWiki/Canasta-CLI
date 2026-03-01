# Email

Canasta requires [`$wgSMTP`](https://www.mediawiki.org/wiki/Manual:$wgSMTP) to be configured in order to send email. This covers account confirmations, password resets, watchlist notifications, and user-to-user email.

## Contents

- [Configuring email](#configuring-email)
- [Disabling email entirely](#disabling-email-entirely)
- [Troubleshooting](#troubleshooting)

---

## Configuring email

Configure [`$wgSMTP`](https://www.mediawiki.org/wiki/Manual:$wgSMTP) in your wiki's settings file to route email through an authenticated SMTP provider.

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

---

## Disabling email entirely

To prevent the wiki from sending any email at all, set [`$wgEnableEmail`](https://www.mediawiki.org/wiki/Manual:$wgEnableEmail) to `false` in your wiki's settings file:

```php
$wgEnableEmail = false;
```

---

## Troubleshooting

### Emails not sent after configuring $wgSMTP

- Verify the SMTP credentials are correct by testing with a simple PHP script
- Check that your hosting provider does not block outbound connections on the SMTP port (465 or 587)
- Look for errors in the MediaWiki debug log (set `$wgDebugLogFile` temporarily)
