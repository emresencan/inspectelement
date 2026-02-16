# Table Actions Catalog

Generated from `TableVerifier.java` and `HtmlTableVerifier.java` public method signatures.

## TableVerifier (Interface)

| Method Signature | Return | Inputs Besides Locator | Only Table Root Locator? | Needs Inner Locator / Row Criteria | Suggestion |
|---|---|---|---|---|---|
| `TableVerifier assertColumnTextEquals(String columnHeader, String expectedText)` | Fluent | String columnHeader, String expectedText | NO | Row criteria | Common |
| `TableVerifier assertHasAnyRow()` | Fluent | - | YES | - | Common |
| `TableVerifier assertRowExists()` | Fluent | - | NO | - | Common |
| `TableVerifier clickButtonInRow(By innerLocator)` | Fluent | By innerLocator | NO | Inner locator | Advanced |
| `TableVerifier clickInColumn(String columnHeader, By innerLocator)` | Fluent | String columnHeader, By innerLocator | NO | Inner locator + row criteria | Advanced |
| `TableVerifier clickInFirstRow(By innerLocator)` | Fluent | By innerLocator | NO | Inner locator | Common |
| `TableVerifier clickInRow(By innerLocator)` | Fluent | By innerLocator | NO | Inner locator | Advanced |
| `TableVerifier clickLink()` | Fluent | - | NO | - | Common |
| `TableVerifier clickRadioInRow()` | Fluent | - | NO | - | Common |
| `TableVerifier filter(String columnHeader, String text)` | Fluent | String columnHeader, String text | NO | Row criteria | Common |
| `String getColumnText(String columnHeader)` | String | String columnHeader | NO | Row criteria | Common |
| `boolean hasAnyRow()` | boolean | - | YES | - | Common |
| `TableVerifier inTable(By tableLocator)` | Fluent | - | YES | - | Common |
| `TableVerifier setInputInColumn(String columnHeader, String text)` | Fluent | String columnHeader, String text | NO | Row criteria | Advanced |
| `TableVerifier whereAllEquals(Map<String, String> columnToExpectedText)` | Fluent | Map<String, String> columnToExpectedText | NO | Row criteria | Advanced |
| `TableVerifier whereContains(String columnHeader, String containedText)` | Fluent | String columnHeader, String containedText | NO | Row criteria | Common |
| `TableVerifier whereEquals(String columnHeader, String expectedText)` | Fluent | String columnHeader, String expectedText | NO | Row criteria | Common |
| `TableVerifier whereMatches(String columnHeader, Predicate<String> predicate)` | Fluent | String columnHeader, Predicate<String> predicate | NO | Row criteria | Advanced |

## HtmlTableVerifier (Implementation)

| Method Signature | Return | Inputs Besides Locator | Only Table Root Locator? | Needs Inner Locator / Row Criteria | Suggestion |
|---|---|---|---|---|---|
| `TableVerifier assertColumnTextEquals(String columnHeader, String expectedText)` | Fluent | String columnHeader, String expectedText | NO | Row criteria | Common |
| `TableVerifier assertHasAnyRow()` | Fluent | - | YES | - | Common |
| `TableVerifier assertRowExists()` | Fluent | - | NO | - | Common |
| `TableVerifier clickButtonInRow(By innerLocator)` | Fluent | By innerLocator | NO | Inner locator | Advanced |
| `TableVerifier clickInColumn(String columnHeader, By innerLocator)` | Fluent | String columnHeader, By innerLocator | NO | Inner locator + row criteria | Advanced |
| `TableVerifier clickInFirstRow(By innerLocator)` | Fluent | By innerLocator | NO | Inner locator | Common |
| `TableVerifier clickInRow(By innerLocator)` | Fluent | By innerLocator | NO | Inner locator | Advanced |
| `TableVerifier clickLink()` | Fluent | - | NO | - | Common |
| `TableVerifier clickRadioInRow()` | Fluent | - | NO | - | Common |
| `TableVerifier filter(String columnHeader, String filterText)` | Fluent | String columnHeader, String filterText | NO | Row criteria | Common |
| `String getColumnText(String columnHeader)` | String | String columnHeader | NO | Row criteria | Common |
| `boolean hasAnyRow()` | boolean | - | YES | - | Common |
| `TableVerifier inTable(By tableLocator)` | Fluent | - | YES | - | Common |
| `TableVerifier setInputInColumn(String columnHeader, String text)` | Fluent | String columnHeader, String text | NO | Row criteria | Advanced |
| `TableVerifier whereAllEquals(Map<String, String> columnToExpectedText)` | Fluent | Map<String, String> columnToExpectedText | NO | Row criteria | Advanced |
| `TableVerifier whereContains(String columnHeader, String containedText)` | Fluent | String columnHeader, String containedText | NO | Row criteria | Common |
| `TableVerifier whereEquals(String columnHeader, String expectedText)` | Fluent | String columnHeader, String expectedText | NO | Row criteria | Common |
| `TableVerifier whereMatches(String columnHeader, Predicate<String> predicate)` | Fluent | String columnHeader, Predicate<String> predicate | NO | Row criteria | Advanced |
