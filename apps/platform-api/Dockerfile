# syntax=docker/dockerfile:1
FROM eclipse-temurin:25-jdk AS build
WORKDIR /src
COPY .mvn .mvn
COPY mvnw pom.xml ./
RUN --mount=type=cache,target=/root/.m2 sh ./mvnw -B -q dependency:go-offline
COPY src src
RUN --mount=type=cache,target=/root/.m2 sh ./mvnw -B -q package -DskipTests \
 && java -Djarmode=tools -jar target/*.jar extract --layers --launcher --destination /out

FROM eclipse-temurin:25-jre
RUN useradd --system --uid 10001 app
WORKDIR /app
COPY --from=build /out/dependencies/ ./
COPY --from=build /out/spring-boot-loader/ ./
COPY --from=build /out/snapshot-dependencies/ ./
COPY --from=build /out/application/ ./
USER 10001
EXPOSE 8080
ENTRYPOINT ["java", "org.springframework.boot.loader.launch.JarLauncher"]
