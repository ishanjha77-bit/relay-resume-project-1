package dev.relay;

import org.springframework.boot.SpringApplication;

public class TestPlatformApiApplication {

	public static void main(String[] args) {
		SpringApplication.from(PlatformApiApplication::main).with(TestcontainersConfiguration.class).run(args);
	}

}
