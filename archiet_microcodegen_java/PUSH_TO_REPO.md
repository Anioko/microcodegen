# Deploying to a separate Maven/GitHub repository

The Java package lives in the monorepo for development but should be published as a separate GitHub repo and to Maven Central.

## Steps — GitHub repo

1. Create: https://github.com/aniekanasuquookono-web/archiet-microcodegen-java

2. Copy these files:
   ```
   archiet_microcodegen_java/pom.xml
   archiet_microcodegen_java/src/
   archiet_microcodegen_java/README.md
   ```

3. Push + tag:
   ```bash
   git init
   git add .
   git commit -m "feat: archiet-microcodegen-java v0.1.0"
   git remote add origin https://github.com/aniekanasuquookono-web/archiet-microcodegen-java.git
   git push -u origin main
   git tag v0.1.0
   git push --tags
   ```

## Build fat JAR

```bash
cd archiet_microcodegen_java
mvn package -q
java -jar target/archiet-microcodegen-java-0.1.0.jar prd.md --out ./myapp/
```

## Maven Central publishing (optional)

To publish to Maven Central, you need:
1. A Sonatype account + namespace `com.archiet`
2. GPG key for signing
3. Configure `~/.m2/settings.xml` with credentials
4. Add the `maven-release-plugin` and deploy with: `mvn deploy -P release`

For MVP distribution, GitHub Releases with the fat JAR is sufficient.
Users download and run with `java -jar archiet-microcodegen-java.jar`.
