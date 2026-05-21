-- People connected ≥12mo ago who matter for outreach.
SELECT
  first_name, last_name, company_name,
  raw_position, role_bucket, seniority,
  industry, employee_band, funding_stage,
  hq_country,
  connected_on,
  linkedin_url
FROM people_enriched
WHERE connected_on <= date('now', '-12 months')
  AND seniority IN ('Director', 'VP', 'C-suite', 'Founder')
  AND role_bucket IN ('Sales', 'BD', 'Marketing', 'Founder')
ORDER BY connected_on ASC;
