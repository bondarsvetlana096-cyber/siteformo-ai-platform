# Multilingual Questionnaire Map

Этот файл фиксирует сокращённый многоязычный опросник для SiteFormo AI Sales Platform.

Поддерживаемые языки:
- EN — English
- DE — Deutsch
- FR — Francais
- IT — Italiano
- ES — Espanol

## Правило реализации
- В backend хранятся стабильные ключи вопросов.
- На frontend / в WhatsApp / в Telegram показывается перевод по выбранному языку.
- Если язык не выбран, используется English как fallback.
- Опросник должен ощущаться коротким и лёгким.
- Логика ветвится по схеме either-or: reference sites или short description.

## Language selector
- `language`
  - EN: Choose your language
  - DE: Wahlen Sie Ihre Sprache
  - FR: Choisissez votre langue
  - IT: Scegli la tua lingua
  - ES: Elige tu idioma

## Compact Questions

### q_business_name
- EN: What is the name of your business?
- DE: Wie heisst Ihr Unternehmen?
- FR: Quel est le nom de votre entreprise ?
- IT: Come si chiama la tua attivita?
- ES: Como se llama tu negocio?

### q_site_type
- EN: What type of website do you need?
- DE: Welche Art von Website brauchen Sie?
- FR: De quel type de site avez-vous besoin ?
- IT: Che tipo di sito ti serve?
- ES: Que tipo de sitio web necesitas?

### q_intake_choice
- EN: Choose the easier option: paste up to 3 websites you like, or briefly describe the website you want.
- DE: Wahlen Sie die einfachere Option: Fugen Sie bis zu 3 Websites ein, die Ihnen gefallen, oder beschreiben Sie kurz die Website, die Sie mochten.
- FR: Choisissez l'option la plus simple : collez jusqu'a 3 sites que vous aimez ou decrivez brievement le site que vous souhaitez.
- IT: Scegli l'opzione piu semplice: inserisci fino a 3 siti che ti piacciono oppure descrivi brevemente il sito che desideri.
- ES: Elige la opcion mas facil: pega hasta 3 sitios que te gusten o describe brevemente el sitio que quieres.

### q_reference_sites
- EN: Paste up to 3 websites you like. These should be reference websites from the internet, not your current website.
- DE: Fugen Sie bis zu 3 Websites ein, die Ihnen gefallen. Das sollen Referenz-Websites aus dem Internet sein, nicht Ihre aktuelle Website.
- FR: Collez jusqu'a 3 sites que vous aimez. Il doit s'agir de sites de reference trouves en ligne, pas de votre site actuel.
- IT: Inserisci fino a 3 siti che ti piacciono. Devono essere siti di riferimento trovati online, non il tuo sito attuale.
- ES: Pega hasta 3 sitios que te gusten. Deben ser sitios de referencia encontrados en internet, no tu sitio actual.

### q_reference_notes
- EN: If you want, briefly say what you like about them: style, structure, hero, colors, effects, popups, premium feeling.
- DE: Wenn Sie mochten, schreiben Sie kurz, was Ihnen daran gefallt: Stil, Struktur, Hero, Farben, Effekte, Popups, Premium-Wirkung.
- FR: Si vous voulez, indiquez brievement ce que vous aimez : style, structure, hero, couleurs, effets, popups, rendu premium.
- IT: Se vuoi, scrivi brevemente cosa ti piace: stile, struttura, hero, colori, effetti, popups, resa premium.
- ES: Si quieres, di brevemente que te gusta: estilo, estructura, hero, colores, efectos, popups, sensacion premium.

### q_desired_site_description
- EN: If you do not have examples, briefly describe the website you want.
- DE: Wenn Sie keine Beispiele haben, beschreiben Sie kurz die Website, die Sie mochten.
- FR: Si vous n'avez pas d'exemples, decrivez brievement le site que vous souhaitez.
- IT: Se non hai esempi, descrivi brevemente il sito che desideri.
- ES: Si no tienes ejemplos, describe brevemente el sitio que quieres.

### q_goal
- EN: Should the website simply exist online or actively bring you clients?
- DE: Soll die Website einfach online sein oder aktiv Kunden bringen?
- FR: Le site doit-il simplement exister en ligne ou attirer activement des clients ?
- IT: Il sito deve solo esistere online o portarti attivamente clienti?
- ES: El sitio debe simplemente existir en linea o atraer clientes activamente?
