# Web Monitoring & SEO

Use `curl` to check sites, monitor uptime, and do basic SEO analysis.

## Check if a site is up

```bash
# quick health check (returns HTTP status code)
curl -s -o /dev/null -w "%{http_code}" https://SITE_URL

# check with response time
curl -s -o /dev/null -w "status: %{http_code}, time: %{time_total}s, size: %{size_download} bytes" https://SITE_URL

# check multiple autoship sites
for slug in app1 app2 app3; do echo -n "$slug.autoship.fun: "; curl -s -o /dev/null -w "%{http_code} (%{time_total}s)" "https://$slug.autoship.fun"; echo; done
```

## Get page title and meta description

```bash
# extract title
curl -s https://SITE_URL | grep -oP '(?<=<title>).*?(?=</title>)' | head -1

# extract meta description
curl -s https://SITE_URL | grep -oP '(?<=<meta name="description" content=")[^"]*' | head -1

# extract Open Graph tags
curl -s https://SITE_URL | grep -oP '(?<=<meta property="og:)[^/]*' | head -5
```

## Check SSL certificate

```bash
# check cert expiry
echo | openssl s_client -servername DOMAIN -connect DOMAIN:443 2>/dev/null | openssl x509 -noout -dates
```

## Monitor GitHub repo traffic (your own repos only)

```bash
# views in last 14 days
gh api repos/OWNER/REPO/traffic/views --jq '{total_views: .count, unique_visitors: .uniques}'

# clones in last 14 days
gh api repos/OWNER/REPO/traffic/clones --jq '{total_clones: .count, unique_cloners: .uniques}'

# top referrers
gh api repos/OWNER/REPO/traffic/popular/referrers --jq '.[] | {referrer: .referrer, count: .count, uniques: .uniques}'

# popular content (which pages people visit)
gh api repos/OWNER/REPO/traffic/popular/paths --jq '.[] | {path: .path, views: .count, uniques: .uniques}'
```

## Check DNS

```bash
# check if domain resolves
dig +short DOMAIN

# check specific record
dig +short DOMAIN A
dig +short DOMAIN CNAME
```

## Tips
- Use web monitoring to verify autoship deployments are healthy
- Track github traffic to see which promotion channels drive the most visitors
- Check referrers to understand where traffic comes from after posting on reddit/HN/twitter
- Monitor competitor repos to understand what's trending
