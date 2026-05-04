/* SiteFormo Demo Registry v1
   Purpose: temporary source of truth for package logic before the main website demo gallery exists.
   Use this file in qwiz1 and extended questionnaire. Later replace placeholder demo URLs with real pages.
*/
(function () {
  const SITEFORMO_PACKAGES = {
    starter: {
      key: 'starter',
      name: 'Starter',
      price: 900,
      depositPercent: 50,
      includedPages: 1,
      delivery: '1–2 business days after design approval',
      marketPosition: 'Simple Irish small-business landing page',
      clientLabel: 'A focused one-page website for a small service or local business.',
      aiScope: [
        'One focused landing page',
        'Clear hero section and call to action',
        'Service explanation',
        'Contact or enquiry form',
        'Mobile-first layout',
        'Basic SEO structure'
      ],
      allowedComplexity: ['landing_page', 'simple_contact_form', 'local_service_intro'],
      defaultExamples: [
        {
          id: 'starter-local-service',
          title: 'Local Service Landing Page',
          url: 'https://siteformo.com/examples/starter-local-service',
          status: 'placeholder',
          packageKey: 'starter',
          summary: 'One-page local service website with clear offer, trust points and contact CTA.'
        },
        {
          id: 'starter-solo-professional',
          title: 'Solo Professional Landing Page',
          url: 'https://siteformo.com/examples/starter-solo-professional',
          status: 'placeholder',
          packageKey: 'starter',
          summary: 'Simple personal/service landing page for consultants, trades or small local providers.'
        }
      ]
    },
    business: {
      key: 'business',
      name: 'Business',
      price: 1500,
      depositPercent: 50,
      includedPages: 3,
      delivery: '3–5 business days after design approval',
      marketPosition: 'Professional Irish service-business website',
      clientLabel: 'A stronger multi-section website for an active small business.',
      aiScope: [
        'Up to 3 core pages or strong homepage sections',
        'Service structure',
        'Trust/reviews area',
        'Lead-generation form',
        'Local business credibility',
        'Better design depth than Starter'
      ],
      allowedComplexity: ['multi_page_service', 'service_business', 'booking_enquiry', 'portfolio_light'],
      defaultExamples: [
        {
          id: 'business-trades-service',
          title: 'Trades / Electrician Business Website',
          url: 'https://siteformo.com/examples/business-trades-service',
          status: 'placeholder',
          packageKey: 'business',
          summary: 'Professional service business website with services, trust, process and quote CTA.'
        },
        {
          id: 'business-cleaning-local',
          title: 'Cleaning / Local Services Website',
          url: 'https://siteformo.com/examples/business-cleaning-local',
          status: 'placeholder',
          packageKey: 'business',
          summary: 'Clear local service website for multiple services and stronger conversion.'
        }
      ]
    },
    premium: {
      key: 'premium',
      name: 'Premium',
      price: 2450,
      depositPercent: 50,
      includedPages: 5,
      delivery: '5–10 business days after design approval',
      marketPosition: 'Premium visual website for a serious SME',
      clientLabel: 'A premium design with stronger brand feel, more pages and better visual polish.',
      aiScope: [
        'Up to 5 pages',
        'Premium art direction',
        'Improved content hierarchy',
        'Reference-inspired design direction',
        'Custom visual sections',
        'Conversion-focused structure'
      ],
      allowedComplexity: ['premium_brand_site', 'reference_inspired', 'portfolio_deep', 'clinic_salon_restaurant_premium'],
      defaultExamples: [
        {
          id: 'premium-brand-service',
          title: 'Premium Brand Service Website',
          url: 'https://siteformo.com/examples/premium-brand-service',
          status: 'placeholder',
          packageKey: 'premium',
          summary: 'Premium multi-page website with strong visuals, sections and reference-inspired design.'
        },
        {
          id: 'premium-restaurant-clinic',
          title: 'Restaurant / Clinic / Salon Premium Website',
          url: 'https://siteformo.com/examples/premium-restaurant-clinic',
          status: 'placeholder',
          packageKey: 'premium',
          summary: 'Premium public-facing business website with richer brand presentation.'
        }
      ]
    },
    custom: {
      key: 'custom',
      name: 'Custom',
      priceFrom: 4500,
      priceTo: 8000,
      depositPercent: 50,
      includedPages: 8,
      delivery: '10–30 business days after design approval, depending on complexity',
      marketPosition: 'Custom SME project, not enterprise marketplace/platform development',
      clientLabel: 'A larger custom website with special structure or advanced requirements.',
      aiScope: [
        'Larger content structure',
        'Advanced planning',
        'Custom sections and templates',
        'Complex page set',
        'Possible integrations after manual review',
        'Manual confirmation before final scope'
      ],
      allowedComplexity: ['custom_sme_site', 'larger_catalog_without_marketplace', 'advanced_content_structure'],
      defaultExamples: [
        {
          id: 'custom-sme-catalog',
          title: 'Custom SME Catalogue Website',
          url: 'https://siteformo.com/examples/custom-sme-catalog',
          status: 'placeholder',
          packageKey: 'custom',
          summary: 'Larger SME website with many sections/pages, but not a marketplace like Amazon.'
        }
      ]
    }
  };

  const BLOCKED_COMPLEX_REFERENCES = [
    'amazon.', 'airbnb.', 'booking.com', 'uber.', 'deliveroo.', 'doordash.',
    'ebay.', 'etsy.', 'facebook.', 'instagram.', 'tiktok.', 'youtube.',
    'netflix.', 'shopify.com', 'stripe.com', 'binance.', 'coinbase.', 'linkedin.'
  ];

  const REFERENCE_UPGRADE_RULES = [
    { pattern: /elegantthemes\.com|awwwards\.com|dribbble\.com|behance\.net/i, packageKey: 'premium', reason: 'This reference requires a more premium visual direction and design depth.' },
    { pattern: /shop|cart|checkout|booking|reservation|membership|dashboard|portal|crm|marketplace/i, packageKey: 'custom', reason: 'This reference suggests advanced functionality or a larger custom project.' },
    { pattern: /restaurant|clinic|salon|hotel|construction|electrician|plumber|cleaning/i, packageKey: 'business', reason: 'This is a standard service-business website scope.' }
  ];

  const PACKAGE_RANK = { starter: 1, business: 2, premium: 3, custom: 4 };

  function normalizePackageKey(value) {
    const v = String(value || '').toLowerCase().trim();
    if (['starter', 'standard', 'basic', 'landing'].includes(v)) return 'starter';
    if (['business', 'company', 'service'].includes(v)) return 'business';
    if (['premium', 'reference', 'wow'].includes(v)) return 'premium';
    if (['custom', 'advanced'].includes(v)) return 'custom';
    return 'business';
  }

  function getPackage(key) {
    return SITEFORMO_PACKAGES[normalizePackageKey(key)];
  }

  function isBlockedComplexReference(text) {
    const value = String(text || '').toLowerCase();
    return BLOCKED_COMPLEX_REFERENCES.some((item) => value.includes(item));
  }

  function detectPackageFromReference(text) {
    const value = String(text || '').trim();
    if (!value) return null;
    if (isBlockedComplexReference(value)) {
      return {
        blocked: true,
        packageKey: 'custom',
        reason: 'We do not build marketplace/platform-scale websites such as Amazon, Airbnb, Uber, Booking or large social networks. SiteFormo works with small and medium businesses.'
      };
    }
    for (const rule of REFERENCE_UPGRADE_RULES) {
      if (rule.pattern.test(value)) {
        return { blocked: false, packageKey: rule.packageKey, reason: rule.reason };
      }
    }
    return null;
  }

  function shouldUpgradePackage(currentPackageKey, suggestedPackageKey) {
    const current = PACKAGE_RANK[normalizePackageKey(currentPackageKey)] || 2;
    const suggested = PACKAGE_RANK[normalizePackageKey(suggestedPackageKey)] || 2;
    return suggested > current;
  }

  function createUpgradeMessage(currentPackageKey, suggestedPackageKey, reason) {
    const current = getPackage(currentPackageKey);
    const next = getPackage(suggestedPackageKey);
    return 'The example you selected looks closer to our ' + next.name + ' package, not ' + current.name + '. ' + reason + ' If you continue, your questions and final balance will be adjusted to the ' + next.name + ' scope.';
  }

  function getClientPackageSummary(packageKey) {
    const pkg = getPackage(packageKey);
    const priceLabel = pkg.price ? ('€' + pkg.price) : ('€' + pkg.priceFrom + '–€' + pkg.priceTo);
    return {
      packageKey: pkg.key,
      name: pkg.name,
      priceLabel,
      depositLabel: pkg.price ? ('€' + Math.round(pkg.price * pkg.depositPercent / 100)) : '50% deposit',
      includedPages: pkg.includedPages,
      delivery: pkg.delivery,
      clientLabel: pkg.clientLabel,
      aiScope: pkg.aiScope,
      examples: pkg.defaultExamples
    };
  }

  window.SiteFormoDemoRegistry = {
    packages: SITEFORMO_PACKAGES,
    normalizePackageKey,
    getPackage,
    getClientPackageSummary,
    detectPackageFromReference,
    shouldUpgradePackage,
    createUpgradeMessage,
    isBlockedComplexReference
  };
})();
